# System Tools Suite

A collection of three educational system tools demonstrating core Linux/Unix concepts including system calls, filesystem traversal, and file integrity.

## Tools Overview

### 1. System Monitor (`system_monitor.py`)
Real-time system resource monitoring using `/proc` filesystem - learn about process monitoring and system calls.

**Features:**
- Real-time CPU usage (total + per-core)
- Memory usage with detailed breakdown
- Disk usage for all mounted filesystems
- Top processes by memory usage
- Uses `/proc/stat`, `/proc/meminfo`, `/proc/mounts`, `/proc/[pid]/`
- Cross-platform compatible (optimized for Linux)

**Usage:**
```bash
./system_monitor.py
# Press 'q' to quit
```

**What You'll Learn:**
- How to read kernel statistics from the `/proc` pseudo-filesystem
- CPU time calculation from jiffies
- Memory management concepts (buffers, cache, RSS)
- Process monitoring via PID directories
- System call conventions (`sysconf` for page size, clock ticks)

---

### 2. Directory Tree Visualizer (`tree_visualizer.py`)
Recursive filesystem traversal with beautiful tree output - learn tree algorithms and directory walking.

**Features:**
- Pretty ASCII/Unicode tree visualization
- Multiple display modes (size, permissions, timestamps)
- Configurable depth limiting
- Pattern filtering with glob support
- Sorting by name, size, or time
- JSON export for programmatic use
- Directory-only mode

**Usage:**
```bash
# Basic tree view
./tree_visualizer.py /path/to/directory

# Include hidden files, show sizes
./tree_visualizer.py -a -s ~/Documents

# Limit depth, show permissions and timestamps
./tree_visualizer.py -d 3 -p -t /var/log

# Sort by size, reversed, only Python files
./tree_visualizer.py --sort size -r -f "*.py" ~/projects

# Export to JSON
./tree_visualizer.py --json tree.json /path/to/dir

# Show only directories
./tree_visualizer.py -L /home/user
```

**What You'll Learn:**
- Tree traversal algorithms (recursive depth-first)
- `os.walk()` and `os.scandir()` for efficient directory scanning
- File metadata extraction (`stat`, `statvfs`)
- Path manipulation with `pathlib`
- Glob pattern matching

---

### 3. Backup Utility (`backup_utility.py`)
Incremental backup with file hashing - learn about data integrity and deduplication.

**Features:**
- MD5 and SHA256 file hashing for integrity verification
- Incremental backups (only changed files)
- Parallel processing with thread pools
- Compression support (gzip)
- Backup manifest tracking (JSON)
- Dry-run mode
- Exclude patterns
- Backup verification

**Usage:**
```bash
# Basic incremental backup
./backup_utility.py ~/Documents /backup/documents

# Compress files larger than 1KB
./backup_utility.py -c ~/Documents /backup/documents

# Dry-run (show what would happen without doing it)
./backup_utility.py -n -v ~/Documents /backup/documents

# Exclude certain patterns
./backup_utility.py -e "*.tmp" -e "*.log" -e ".git" ~/project /backup/project

# Use more threads for faster processing
./backup_utility.py -t 8 ~/LargeFolder /backup/large

# Verify existing backup integrity
./backup_utility.py --verify /backup/documents/.backup_manifest.json
```

**What You'll Learn:**
- Hash algorithms (MD5, SHA256) for file integrity
- Incremental backup strategies
- Manifest-based tracking
- Parallel I/O with `ThreadPoolExecutor`
- File compression with gzip
- Deduplication concepts

---

## Technical Concepts Covered

### System Calls Used
- `read()` - Reading from `/proc` files
- `stat()` / `fstat()` - File metadata
- `statvfs()` - Filesystem statistics
- `sysconf()` - System configuration values
- `opendir()` / `readdir()` - Directory traversal (via Python's os.scandir)

### /proc Filesystem
The system monitor demonstrates reading from these special files:
- `/proc/stat` - CPU statistics
- `/proc/meminfo` - Memory information
- `/proc/mounts` - Mounted filesystems
- `/proc/[pid]/status` - Process status
- `/proc/[pid]/stat` - Process statistics

### Algorithms
- **Tree visualization**: Recursive depth-first traversal with prefix tracking
- **File hashing**: Streaming MD5/SHA256 with chunked reading (64KB blocks)
- **Incremental backup**: Hash-based change detection with metadata comparison
- **Parallel processing**: ThreadPoolExecutor for concurrent file operations

## Requirements

- Python 3.7+
- Linux (system monitor optimized for Linux; other tools work on macOS/Windows too)

## Installation

```bash
chmod +x system_monitor.py tree_visualizer.py backup_utility.py
```

## Example Workflows

### Monitor System Resources
```bash
./system_monitor.py
```

### Create a Project Snapshot
```bash
# Create a compressed backup with verification
./backup_utility.py -c -v ~/myproject /backup/myproject-$(date +%Y%m%d)
./backup_utility.py --verify /backup/myproject-$(date +%Y%m%d)/.backup_manifest.json
```

### Analyze Directory Structure
```bash
# Find largest files in a directory
./tree_visualizer.py --sort size -r -s ~/Downloads | head -20

# Find all Python files in a project
./tree_visualizer.py -f "*.py" ~/myproject

# Export directory structure for documentation
./tree_visualizer.py --json structure.json ~/myproject
```

## Learning Path

1. **Start with Tree Visualizer** - Understand filesystem traversal
2. **Try System Monitor** - Learn about `/proc` and process monitoring  
3. **Use Backup Utility** - Understand hashing, integrity, and incremental operations

Each tool includes extensive comments explaining the "why" behind implementation choices.
