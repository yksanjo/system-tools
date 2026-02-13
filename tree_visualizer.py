#!/usr/bin/env python3
"""
Directory Tree Visualizer - Recursive file system traversal with pretty output.
Demonstrates tree traversal algorithms and filesystem operations.
"""

import os
import sys
import stat
import argparse
from datetime import datetime
from pathlib import Path
from typing import Iterator, Tuple, Optional, List, Dict
from dataclasses import dataclass, field
import json


@dataclass
class FileInfo:
    """Information about a file or directory."""
    path: Path
    name: str
    is_dir: bool
    size: int
    modified: float
    permissions: str
    depth: int
    children: List['FileInfo'] = field(default_factory=list)
    
    @property
    def modified_str(self) -> str:
        """Get formatted modification time."""
        return datetime.fromtimestamp(self.modified).strftime('%Y-%m-%d %H:%M')
    
    @property
    def size_str(self) -> str:
        """Get human-readable size."""
        if self.is_dir:
            return "-"
        return self._format_size(self.size)
    
    @staticmethod
    def _format_size(size: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ['B', 'K', 'M', 'G', 'T']:
            if size < 1024:
                return f"{size:.1f}{unit}" if unit != 'B' else f"{size}B"
            size /= 1024
        return f"{size:.1f}P"


class TreeVisualizer:
    """Visualize directory structure as a tree."""
    
    # Unicode box-drawing characters
    BRANCH = "├── "
    LAST_BRANCH = "└── "
    VERTICAL = "│   "
    SPACE = "    "
    
    def __init__(self, 
                 max_depth: Optional[int] = None,
                 show_hidden: bool = False,
                 show_size: bool = True,
                 show_date: bool = False,
                 show_permissions: bool = False,
                 filter_pattern: Optional[str] = None,
                 sort_by: str = 'name',
                 reverse: bool = False,
                 only_dirs: bool = False,
                 summary: bool = True):
        self.max_depth = max_depth
        self.show_hidden = show_hidden
        self.show_size = show_size
        self.show_date = show_date
        self.show_permissions = show_permissions
        self.filter_pattern = filter_pattern
        self.sort_by = sort_by
        self.reverse = reverse
        self.only_dirs = only_dirs
        self.summary = summary
        
        self.stats = {
            'dirs': 0,
            'files': 0,
            'total_size': 0,
            'max_depth_reached': 0,
        }
    
    def get_permissions(self, path: Path) -> str:
        """Get file permissions as a string (like ls -l)."""
        try:
            mode = path.stat().st_mode
            perms = []
            perms.append('d' if stat.S_ISDIR(mode) else '-')
            perms.append('r' if mode & stat.S_IRUSR else '-')
            perms.append('w' if mode & stat.S_IWUSR else '-')
            perms.append('x' if mode & stat.S_IXUSR else '-')
            perms.append('r' if mode & stat.S_IRGRP else '-')
            perms.append('w' if mode & stat.S_IWGRP else '-')
            perms.append('x' if mode & stat.S_IXGRP else '-')
            perms.append('r' if mode & stat.S_IROTH else '-')
            perms.append('w' if mode & stat.S_IWOTH else '-')
            perms.append('x' if mode & stat.S_IXOTH else '-')
            return ''.join(perms)
        except (OSError, PermissionError):
            return "?---------"
    
    def should_include(self, path: Path) -> bool:
        """Check if a path should be included based on filters."""
        # Hidden files check
        if not self.show_hidden and path.name.startswith('.'):
            return False
        
        # Pattern filter
        if self.filter_pattern and not path.match(self.filter_pattern):
            return False
        
        # Only directories
        if self.only_dirs and not path.is_dir():
            return False
        
        return True
    
    def scan_directory(self, path: Path, depth: int = 0) -> Optional[FileInfo]:
        """Recursively scan a directory and build tree structure."""
        if self.max_depth is not None and depth > self.max_depth:
            return None
        
        self.stats['max_depth_reached'] = max(self.stats['max_depth_reached'], depth)
        
        try:
            stat_info = path.stat()
            is_dir = path.is_dir()
        except (OSError, PermissionError) as e:
            return FileInfo(
                path=path,
                name=path.name,
                is_dir=False,
                size=0,
                modified=0,
                permissions="?---------",
                depth=depth,
            )
        
        file_info = FileInfo(
            path=path,
            name=path.name,
            is_dir=is_dir,
            size=stat_info.st_size,
            modified=stat_info.st_mtime,
            permissions=self.get_permissions(path),
            depth=depth,
        )
        
        if is_dir:
            self.stats['dirs'] += 1
            try:
                entries = list(os.scandir(path))
                
                # Filter and sort entries
                children = []
                for entry in entries:
                    entry_path = Path(entry.path)
                    if self.should_include(entry_path):
                        child = self.scan_directory(entry_path, depth + 1)
                        if child:
                            children.append(child)
                
                # Sort children
                reverse = self.reverse
                if self.sort_by == 'name':
                    children.sort(key=lambda x: (not x.is_dir, x.name.lower()), reverse=reverse)
                elif self.sort_by == 'size':
                    children.sort(key=lambda x: (not x.is_dir, x.size), reverse=not reverse)
                elif self.sort_by == 'time':
                    children.sort(key=lambda x: x.modified, reverse=not reverse)
                
                file_info.children = children
                
            except PermissionError:
                pass
        else:
            self.stats['files'] += 1
            self.stats['total_size'] += stat_info.st_size
        
        return file_info
    
    def render_tree(self, node: FileInfo, prefix: str = "", is_last: bool = True) -> Iterator[str]:
        """Render tree as lines with proper indentation."""
        if node.depth == 0:
            # Root node
            yield self.format_line(node, "", True)
            prefix = ""
        
        children = node.children
        for i, child in enumerate(children):
            is_last_child = i == len(children) - 1
            branch = self.LAST_BRANCH if is_last_child else self.BRANCH
            
            yield self.format_line(child, prefix + branch, is_last_child)
            
            if child.children:
                extension = self.SPACE if is_last_child else self.VERTICAL
                yield from self.render_tree(child, prefix + extension, is_last_child)
    
    def format_line(self, node: FileInfo, prefix: str, is_last: bool) -> str:
        """Format a single tree line with optional metadata."""
        parts = [prefix]
        
        # Add metadata columns
        if self.show_permissions:
            parts.append(f"[{node.permissions}] ")
        
        if self.show_date:
            parts.append(f"{node.modified_str} ")
        
        if self.show_size and not node.is_dir:
            parts.append(f"({node.size_str:>8}) ")
        
        # Name with styling
        name = node.name
        if node.is_dir:
            name = f"📁 {name}/"
        else:
            name = f"📄 {name}"
        
        parts.append(name)
        
        return ''.join(parts)
    
    def print_tree(self, root_path: str):
        """Main entry point to visualize a directory tree."""
        path = Path(root_path).resolve()
        
        if not path.exists():
            print(f"Error: Path '{root_path}' does not exist.", file=sys.stderr)
            return 1
        
        print(f"\n Directory: {path}")
        print(f"{'=' * 60}\n")
        
        # Scan and build tree
        root = self.scan_directory(path)
        
        if root:
            # Render tree
            for line in self.render_tree(root):
                print(line)
        
        # Print summary
        if self.summary:
            print(f"\n{'=' * 60}")
            print(f" Summary:")
            print(f"   Directories: {self.stats['dirs']}")
            print(f"   Files: {self.stats['files']}")
            print(f"   Total size: {FileInfo._format_size(None, self.stats['total_size'])}")
            print(f"   Max depth reached: {self.stats['max_depth_reached']}")
        
        return 0
    
    def export_json(self, root_path: str, output_file: str):
        """Export tree structure as JSON."""
        path = Path(root_path).resolve()
        root = self.scan_directory(path)
        
        def node_to_dict(node: FileInfo) -> Dict:
            result = {
                'name': node.name,
                'is_dir': node.is_dir,
                'size': node.size,
                'modified': node.modified,
                'permissions': node.permissions,
            }
            if node.children:
                result['children'] = [node_to_dict(c) for c in node.children]
            return result
        
        if root:
            data = node_to_dict(root)
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"Tree exported to {output_file}")


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description='Directory Tree Visualizer - Pretty file system traversal',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /home/user                    # Basic tree view
  %(prog)s -a /home/user                 # Include hidden files
  %(prog)s -d 3 /home/user               # Limit depth to 3 levels
  %(prog)s -s -t /home/user              # Show size and modification time
  %(prog)s -L /home/user                 # Show only directories
  %(prog)s --sort size -r /home/user     # Sort by size, reversed
  %(prog)s -p "*.py" /home/user          # Filter for Python files
        """
    )
    
    parser.add_argument('path', nargs='?', default='.',
                        help='Directory to visualize (default: current directory)')
    parser.add_argument('-a', '--all', action='store_true',
                        help='Include hidden files (starting with .)')
    parser.add_argument('-d', '--depth', type=int, metavar='N',
                        help='Maximum depth to traverse')
    parser.add_argument('-s', '--size', action='store_true',
                        help='Show file sizes')
    parser.add_argument('-t', '--time', action='store_true',
                        help='Show modification time')
    parser.add_argument('-p', '--permissions', action='store_true',
                        help='Show file permissions')
    parser.add_argument('-f', '--filter', metavar='PATTERN',
                        help='Filter files by glob pattern (e.g., "*.py")')
    parser.add_argument('--sort', choices=['name', 'size', 'time'], default='name',
                        help='Sort entries by name, size, or time (default: name)')
    parser.add_argument('-r', '--reverse', action='store_true',
                        help='Reverse sort order')
    parser.add_argument('-L', '--only-dirs', action='store_true',
                        help='Show only directories')
    parser.add_argument('--no-summary', action='store_true',
                        help='Hide summary statistics')
    parser.add_argument('--json', metavar='FILE',
                        help='Export tree as JSON to FILE')
    
    args = parser.parse_args()
    
    # Enable size display if sorting by size
    show_size = args.size or args.sort == 'size'
    
    visualizer = TreeVisualizer(
        max_depth=args.depth,
        show_hidden=args.all,
        show_size=show_size,
        show_date=args.time,
        show_permissions=args.permissions,
        filter_pattern=args.filter,
        sort_by=args.sort,
        reverse=args.reverse,
        only_dirs=args.only_dirs,
        summary=not args.no_summary,
    )
    
    if args.json:
        visualizer.export_json(args.path, args.json)
    else:
        return visualizer.print_tree(args.path)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
