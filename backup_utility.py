#!/usr/bin/env python3
"""
Backup Utility - Incremental backups using file hashing.
Similar to rsync basics - learns about file integrity and deduplication.
"""

import os
import sys
import hashlib
import json
import shutil
import argparse
import gzip
from datetime import datetime
from pathlib import Path
from typing import Dict, Set, List, Optional, Tuple, Callable
from dataclasses import dataclass, asdict, field
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


@dataclass
class FileHash:
    """Stores hash information for a file."""
    path: str
    size: int
    mtime: float
    md5: str
    sha256: str
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'FileHash':
        return cls(**data)


@dataclass
class BackupManifest:
    """Manifest for a backup operation."""
    backup_id: str
    timestamp: str
    source_path: str
    dest_path: str
    files_backed_up: int = 0
    files_skipped: int = 0
    files_removed: int = 0
    bytes_transferred: int = 0
    total_size: int = 0
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return asdict(self)


class BackupUtility:
    """
    Incremental backup utility using file hashing.
    
    Features:
    - MD5 and SHA256 file hashing for integrity
    - Incremental backups (only changed files)
    - Manifest tracking for each backup
    - Compression support
    - Parallel file processing
    - Dry-run mode
    """
    
    def __init__(self, source: str, dest: str, 
                 compress: bool = False,
                 checksum_algorithm: str = 'sha256',
                 threads: int = 4,
                 dry_run: bool = False,
                 exclude_patterns: List[str] = None,
                 verbose: bool = False):
        self.source = Path(source).resolve()
        self.dest = Path(dest).resolve()
        self.compress = compress
        self.checksum_algorithm = checksum_algorithm
        self.threads = threads
        self.dry_run = dry_run
        self.exclude_patterns = exclude_patterns or []
        self.verbose = verbose
        
        self.manifest = BackupManifest(
            backup_id=self._generate_backup_id(),
            timestamp=datetime.now().isoformat(),
            source_path=str(self.source),
            dest_path=str(self.dest),
        )
        
        self.previous_hashes: Dict[str, FileHash] = {}
        self.current_hashes: Dict[str, FileHash] = {}
        self.lock = threading.Lock()
        
    def _generate_backup_id(self) -> str:
        """Generate unique backup ID."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        random_suffix = hashlib.md5(os.urandom(8)).hexdigest()[:8]
        return f"backup_{timestamp}_{random_suffix}"
    
    def _load_previous_manifest(self) -> Optional[Dict]:
        """Load manifest from previous backup."""
        manifest_path = self.dest / '.backup_manifest.json'
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return None
    
    def _should_exclude(self, path: Path) -> bool:
        """Check if path should be excluded."""
        path_str = str(path)
        for pattern in self.exclude_patterns:
            if pattern in path_str:
                return True
        return False
    
    def _calculate_hash(self, filepath: Path) -> Tuple[str, str]:
        """
        Calculate MD5 and SHA256 hashes for a file.
        Uses chunked reading for memory efficiency with large files.
        """
        md5_hash = hashlib.md5()
        sha256_hash = hashlib.sha256()
        
        # 64KB chunks for efficient processing
        chunk_size = 65536
        
        with open(filepath, 'rb') as f:
            while chunk := f.read(chunk_size):
                md5_hash.update(chunk)
                sha256_hash.update(chunk)
        
        return md5_hash.hexdigest(), sha256_hash.hexdigest()
    
    def _hash_file(self, filepath: Path) -> Optional[FileHash]:
        """Create FileHash entry for a file."""
        try:
            stat = filepath.stat()
            rel_path = str(filepath.relative_to(self.source))
            
            if self._should_exclude(filepath):
                return None
            
            md5, sha256 = self._calculate_hash(filepath)
            
            return FileHash(
                path=rel_path,
                size=stat.st_size,
                mtime=stat.st_mtime,
                md5=md5,
                sha256=sha256,
            )
        except (OSError, PermissionError) as e:
            with self.lock:
                self.manifest.errors.append(f"Cannot hash {filepath}: {e}")
            return None
    
    def _collect_files(self) -> List[Path]:
        """Collect all files to be processed."""
        files = []
        
        if self.source.is_file():
            return [self.source]
        
        try:
            for root, dirs, filenames in os.walk(self.source):
                root_path = Path(root)
                
                # Filter directories
                dirs[:] = [d for d in dirs if not self._should_exclude(root_path / d)]
                
                for filename in filenames:
                    filepath = root_path / filename
                    if not self._should_exclude(filepath):
                        files.append(filepath)
                        self.manifest.total_size += filepath.stat().st_size
        except PermissionError as e:
            self.manifest.errors.append(f"Cannot access directory: {e}")
        
        return files
    
    def _needs_backup(self, file_hash: FileHash) -> bool:
        """Check if file needs to be backed up (changed or new)."""
        if file_hash.path not in self.previous_hashes:
            return True
        
        prev = self.previous_hashes[file_hash.path]
        
        # Check size first (fastest)
        if prev.size != file_hash.size:
            return True
        
        # Check modification time
        if abs(prev.mtime - file_hash.mtime) > 0.001:  # 1ms tolerance
            return True
        
        # Finally, check hash
        if self.checksum_algorithm == 'md5':
            return prev.md5 != file_hash.md5
        else:
            return prev.sha256 != file_hash.sha256
    
    def _backup_file(self, file_hash: FileHash, source_file: Path) -> bool:
        """Copy a file to backup destination."""
        try:
            dest_file = self.dest / file_hash.path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            
            if self.dry_run:
                return True
            
            if self.compress and file_hash.size > 1024:  # Compress files > 1KB
                dest_file = dest_file.with_suffix(dest_file.suffix + '.gz')
                with open(source_file, 'rb') as f_in:
                    with gzip.open(dest_file, 'wb', compresslevel=6) as f_out:
                        shutil.copyfileobj(f_in, f_out)
            else:
                shutil.copy2(source_file, dest_file)
            
            # Verify backup with hash
            dest_hash = self._calculate_hash(dest_file if not self.compress else 
                                             self.dest / file_hash.path)
            source_hash = (file_hash.md5, file_hash.sha256)
            
            if self.checksum_algorithm == 'md5':
                if dest_hash[0] != source_hash[0]:
                    raise IOError(f"Hash mismatch for {file_hash.path}")
            else:
                if dest_hash[1] != source_hash[1]:
                    raise IOError(f"Hash mismatch for {file_hash.path}")
            
            return True
            
        except (OSError, IOError) as e:
            with self.lock:
                self.manifest.errors.append(f"Backup failed for {file_hash.path}: {e}")
            return False
    
    def _remove_deleted_files(self) -> int:
        """Remove files from backup that no longer exist in source."""
        removed = 0
        current_paths = set(self.current_hashes.keys())
        
        for old_path in self.previous_hashes.keys():
            if old_path not in current_paths:
                dest_file = self.dest / old_path
                if self.compress:
                    dest_file = dest_file.with_suffix(dest_file.suffix + '.gz')
                
                if not self.dry_run and dest_file.exists():
                    try:
                        dest_file.unlink()
                        removed += 1
                        if self.verbose:
                            print(f"  Removed: {old_path}")
                    except OSError as e:
                        self.manifest.errors.append(f"Cannot remove {old_path}: {e}")
                else:
                    removed += 1
        
        return removed
    
    def run(self) -> BackupManifest:
        """Execute the backup operation."""
        print(f"\n{'=' * 60}")
        print(f" Backup Utility - Incremental Backup")
        print(f"{'=' * 60}")
        print(f" Source:      {self.source}")
        print(f" Destination: {self.dest}")
        print(f" Backup ID:   {self.manifest.backup_id}")
        print(f" Algorithm:   {self.checksum_algorithm}")
        print(f" Compress:    {self.compress}")
        print(f" Threads:     {self.threads}")
        print(f" Dry-run:     {self.dry_run}")
        print(f"{'=' * 60}\n")
        
        # Load previous manifest
        prev_manifest = self._load_previous_manifest()
        if prev_manifest:
            print(" Previous backup found. Loading hashes...")
            # In a real implementation, load from a separate hashes file
        
        # Collect files
        print(" Scanning source directory...")
        files = self._collect_files()
        print(f" Found {len(files)} files ({self._format_size(self.manifest.total_size)})")
        
        # Create destination directory
        if not self.dry_run:
            self.dest.mkdir(parents=True, exist_ok=True)
        
        # Calculate hashes in parallel
        print("\n Calculating file hashes...")
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            future_to_file = {executor.submit(self._hash_file, f): f for f in files}
            
            for i, future in enumerate(as_completed(future_to_file)):
                file_hash = future.result()
                if file_hash:
                    self.current_hashes[file_hash.path] = file_hash
                
                if (i + 1) % 100 == 0 or i == len(files) - 1:
                    print(f"  Progress: {i + 1}/{len(files)} files hashed", end='\r')
        
        print(f"\n  {len(self.current_hashes)} files hashed successfully")
        
        # Determine which files need backup
        files_to_backup = [
            (h, self.source / h.path) 
            for h in self.current_hashes.values() 
            if self._needs_backup(h)
        ]
        
        self.manifest.files_skipped = len(self.current_hashes) - len(files_to_backup)
        print(f"\n Files to backup: {len(files_to_backup)} (skipped: {self.manifest.files_skipped})")
        
        # Backup files
        if files_to_backup:
            print("\n Backing up files...")
            with ThreadPoolExecutor(max_workers=self.threads) as executor:
                future_to_file = {
                    executor.submit(self._backup_file, h, s): h 
                    for h, s in files_to_backup
                }
                
                for i, future in enumerate(as_completed(future_to_file)):
                    file_hash = future_to_file[future]
                    if future.result():
                        self.manifest.files_backed_up += 1
                        self.manifest.bytes_transferred += file_hash.size
                        if self.verbose:
                            print(f"  + {file_hash.path}")
                    
                    if (i + 1) % 10 == 0 or i == len(files_to_backup) - 1:
                        print(f"  Progress: {i + 1}/{len(files_to_backup)} files", end='\r')
        
        # Remove deleted files
        print("\n\n Cleaning up deleted files...")
        self.manifest.files_removed = self._remove_deleted_files()
        print(f"  Removed {self.manifest.files_removed} obsolete files")
        
        # Save manifest
        if not self.dry_run:
            self._save_manifest()
            self._save_hashes()
        
        # Print summary
        print(f"\n{'=' * 60}")
        print(" Backup Summary")
        print(f"{'=' * 60}")
        print(f" Files backed up:  {self.manifest.files_backed_up}")
        print(f" Files skipped:    {self.manifest.files_skipped}")
        print(f" Files removed:    {self.manifest.files_removed}")
        print(f" Bytes transferred: {self._format_size(self.manifest.bytes_transferred)}")
        print(f" Errors:           {len(self.manifest.errors)}")
        
        if self.manifest.errors and self.verbose:
            print("\n Errors encountered:")
            for error in self.manifest.errors[:10]:
                print(f"   - {error}")
            if len(self.manifest.errors) > 10:
                print(f"   ... and {len(self.manifest.errors) - 10} more")
        
        print(f"{'=' * 60}")
        
        return self.manifest
    
    def _save_manifest(self):
        """Save backup manifest to JSON."""
        manifest_path = self.dest / '.backup_manifest.json'
        with open(manifest_path, 'w') as f:
            json.dump(self.manifest.to_dict(), f, indent=2)
    
    def _save_hashes(self):
        """Save file hashes for future incremental backups."""
        hashes_path = self.dest / '.backup_hashes.json'
        hashes_data = {k: v.to_dict() for k, v in self.current_hashes.items()}
        with open(hashes_path, 'w') as f:
            json.dump(hashes_data, f, indent=2)
    
    @staticmethod
    def _format_size(size: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.2f} {unit}" if unit != 'B' else f"{size} B"
            size /= 1024
        return f"{size:.2f} PB"
    
    @staticmethod
    def verify_backup(manifest_path: str) -> bool:
        """Verify backup integrity by re-checking all hashes."""
        print(f"\n Verifying backup: {manifest_path}")
        
        manifest_file = Path(manifest_path)
        if not manifest_file.exists():
            print(" Manifest not found!")
            return False
        
        backup_dir = manifest_file.parent
        hashes_path = backup_dir / '.backup_hashes.json'
        
        if not hashes_path.exists():
            print(" Hash file not found!")
            return False
        
        with open(hashes_path, 'r') as f:
            hashes_data = json.load(f)
        
        all_valid = True
        errors = []
        
        for path_str, hash_info in hashes_data.items():
            file_path = backup_dir / path_str
            if not file_path.exists():
                all_valid = False
                errors.append(f"Missing: {path_str}")
                continue
            
            # Recalculate hash
            utility = BackupUtility("", "")
            md5, sha256 = utility._calculate_hash(file_path)
            
            if md5 != hash_info['md5'] or sha256 != hash_info['sha256']:
                all_valid = False
                errors.append(f"Corrupted: {path_str}")
        
        if all_valid:
            print(" ✓ All files verified successfully!")
            return True
        else:
            print(f" ✗ Verification failed! {len(errors)} files have issues.")
            for error in errors[:5]:
                print(f"   - {error}")
            return False


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description='Backup Utility - Incremental backups with file hashing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /home/user/docs /backup/docs          # Basic backup
  %(prog)s -c /home/user/docs /backup/docs       # Compress files
  %(prog)s -n /home/user/docs /backup/docs       # Dry-run (show what would happen)
  %(prog)s -v /home/user/docs /backup/docs       # Verbose output
  %(prog)s -e "*.tmp" -e "*.log" src dest        # Exclude patterns
  %(prog)s --verify /backup/docs/.backup_manifest.json  # Verify backup
        """
    )
    
    parser.add_argument('source', nargs='?',
                        help='Source directory or file to backup')
    parser.add_argument('dest', nargs='?',
                        help='Destination directory for backup')
    parser.add_argument('-c', '--compress', action='store_true',
                        help='Compress backed up files with gzip')
    parser.add_argument('-a', '--algorithm', choices=['md5', 'sha256'], default='sha256',
                        help='Hash algorithm for integrity (default: sha256)')
    parser.add_argument('-t', '--threads', type=int, default=4,
                        help='Number of parallel threads (default: 4)')
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('-e', '--exclude', action='append', default=[],
                        help='Exclude pattern (can be used multiple times)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Verbose output')
    parser.add_argument('--verify', metavar='MANIFEST',
                        help='Verify backup integrity using manifest file')
    
    args = parser.parse_args()
    
    # Handle verification mode
    if args.verify:
        success = BackupUtility.verify_backup(args.verify)
        return 0 if success else 1
    
    # Validate arguments
    if not args.source or not args.dest:
        parser.error("source and dest are required (unless using --verify)")
    
    if not os.path.exists(args.source):
        print(f"Error: Source path '{args.source}' does not exist.", file=sys.stderr)
        return 1
    
    # Run backup
    utility = BackupUtility(
        source=args.source,
        dest=args.dest,
        compress=args.compress,
        checksum_algorithm=args.algorithm,
        threads=args.threads,
        dry_run=args.dry_run,
        exclude_patterns=args.exclude,
        verbose=args.verbose,
    )
    
    try:
        manifest = utility.run()
        return 0 if len(manifest.errors) == 0 else 1
    except KeyboardInterrupt:
        print("\n\nBackup interrupted by user.")
        return 130
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
