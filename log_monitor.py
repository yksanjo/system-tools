#!/usr/bin/env python3
"""
Log Monitor - Real-time log file tailing with filtering and highlighting.
Similar to 'tail -f' but with powerful filtering, regex support, and highlighting.
"""

import os
import sys
import re
import argparse
import select
import termios
import tty
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Pattern, Optional, Callable, Set
from dataclasses import dataclass, field
from collections import deque
import signal


@dataclass
class LogEntry:
    """Represents a single log entry."""
    timestamp: str
    level: str
    message: str
    raw: str
    source: str = ""
    matched: bool = True


@dataclass
class HighlightRule:
    """Color highlighting rule for log entries."""
    pattern: Pattern
    color: str
    priority: int = 0


class Colors:
    """ANSI color codes."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Standard colors
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    
    # Background colors
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    
    # Log level colors
    LEVEL_COLORS = {
        'ERROR': RED,
        'WARN': YELLOW,
        'WARNING': YELLOW,
        'INFO': GREEN,
        'DEBUG': BLUE,
        'TRACE': DIM,
        'FATAL': BG_RED + WHITE + BOLD,
        'CRITICAL': BG_RED + WHITE + BOLD,
    }


class LogMonitor:
    """
    Real-time log file monitor with filtering and highlighting.
    
    Features:
    - Follow mode like 'tail -f'
    - Multiple filter patterns (include/exclude)
    - Regex support
    - Color highlighting by log level
    - Custom highlight patterns
    - Multiple file monitoring
    - Statistics tracking
    """
    
    # Common log formats
    LOG_PATTERNS = [
        # ISO 8601: 2024-01-15T10:30:45.123Z
        (r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(\w+)\s+(.*)$', 
         'iso8601'),
        # Standard: Jan 15 10:30:45
        (r'^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\w+)\s+(.*)$', 
         'syslog'),
        # Apache/Nginx: 127.0.0.1 - - [15/Jan/2024:10:30:45 +0000]
        (r'^(.*?)\s+\[(\d{2}/\w+/\d{4}:\d{2}:\d{2}:\d{2}\s+[+-]\d{4})\]\s+(.*)$', 
         'apache'),
        # Simple: [2024-01-15 10:30:45] [INFO] message
        (r'^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s+\[(\w+)\]\s+(.*)$', 
         'bracketed'),
        # Custom level prefix: INFO: message or [INFO] message
        (r'^(?:\[(\w+)\]|(\w+):)\s+(.*)$', 
         'prefixed'),
    ]
    
    def __init__(self,
                 files: List[str],
                 follow: bool = True,
                 lines: int = 10,
                 include_patterns: List[str] = None,
                 exclude_patterns: List[str] = None,
                 case_sensitive: bool = False,
                 regex: bool = True,
                 no_color: bool = False,
                 timestamp: bool = False,
                 highlight_rules: List[str] = None,
                 stats: bool = False,
                 save_to: str = None):
        self.files = files
        self.follow = follow
        self.lines = lines
        self.case_sensitive = case_sensitive
        self.use_regex = regex
        self.no_color = no_color or not sys.stdout.isatty()
        self.show_timestamp = timestamp
        self.stats_enabled = stats
        self.save_path = save_to
        
        self.running = True
        self.file_positions: Dict[str, int] = {}
        self.inode_map: Dict[str, int] = {}
        
        # Compile filters
        self.include_filters: List[Pattern] = []
        self.exclude_filters: List[Pattern] = []
        self._compile_filters(include_patterns or [], exclude_patterns or [])
        
        # Highlight rules
        self.highlight_rules: List[HighlightRule] = []
        self._setup_highlighting(highlight_rules or [])
        
        # Statistics
        self.stats = {
            'total_lines': 0,
            'matched_lines': 0,
            'by_level': {},
            'by_file': {},
            'start_time': datetime.now(),
        }
        
        # Ring buffer for recent lines (for context)
        self.context_buffer: deque = deque(maxlen=100)
        
        # Output file
        self.output_file: Optional[object] = None
        if save_to:
            self.output_file = open(save_to, 'a')
    
    def _compile_filters(self, include: List[str], exclude: List[str]):
        """Compile filter patterns."""
        flags = 0 if self.case_sensitive else re.IGNORECASE
        
        for pattern in include:
            if self.use_regex:
                self.include_filters.append(re.compile(pattern, flags))
            else:
                self.include_filters.append(re.compile(re.escape(pattern), flags))
        
        for pattern in exclude:
            if self.use_regex:
                self.exclude_filters.append(re.compile(pattern, flags))
            else:
                self.exclude_filters.append(re.compile(re.escape(pattern), flags))
    
    def _setup_highlighting(self, rules: List[str]):
        """Setup custom highlight rules."""
        color_map = {
            'red': Colors.RED,
            'green': Colors.GREEN,
            'yellow': Colors.YELLOW,
            'blue': Colors.BLUE,
            'magenta': Colors.MAGENTA,
            'cyan': Colors.CYAN,
            'bg_red': Colors.BG_RED,
            'bg_green': Colors.BG_GREEN,
        }
        
        # Default level highlighting
        for level, color in Colors.LEVEL_COLORS.items():
            self.highlight_rules.append(HighlightRule(
                pattern=re.compile(rf'\b{level}\b', re.IGNORECASE),
                color=color,
                priority=1
            ))
        
        # Custom rules
        for rule in rules:
            if ':' in rule:
                pattern, color_name = rule.rsplit(':', 1)
                if color_name in color_map:
                    self.highlight_rules.append(HighlightRule(
                        pattern=re.compile(pattern, re.IGNORECASE),
                        color=color_map[color_name],
                        priority=2
                    ))
    
    def _parse_log_line(self, line: str, source: str = "") -> LogEntry:
        """Parse a log line to extract timestamp, level, and message."""
        line = line.rstrip('\n\r')
        
        for pattern, fmt_name in self.LOG_PATTERNS:
            match = re.match(pattern, line)
            if match:
                groups = match.groups()
                if fmt_name == 'prefixed':
                    level = groups[0] or groups[1]
                    message = groups[2]
                    timestamp = datetime.now().strftime('%H:%M:%S')
                elif fmt_name == 'apache':
                    timestamp = groups[1]
                    level = 'ACCESS'
                    message = groups[0] + ' ' + groups[2]
                else:
                    timestamp = groups[0]
                    level = groups[1].upper()
                    message = groups[2]
                
                return LogEntry(
                    timestamp=timestamp,
                    level=level,
                    message=message,
                    raw=line,
                    source=source
                )
        
        # Default: treat entire line as message
        return LogEntry(
            timestamp=datetime.now().strftime('%H:%M:%S'),
            level='UNKNOWN',
            message=line,
            raw=line,
            source=source
        )
    
    def _should_display(self, entry: LogEntry) -> bool:
        """Check if log entry matches filters."""
        text = entry.raw
        
        # Check exclude filters first
        for pattern in self.exclude_filters:
            if pattern.search(text):
                return False
        
        # If include filters exist, must match at least one
        if self.include_filters:
            for pattern in self.include_filters:
                if pattern.search(text):
                    return True
            return False
        
        return True
    
    def _colorize(self, text: str, level: str = '') -> str:
        """Apply color highlighting to text."""
        if self.no_color:
            return text
        
        # Apply level color
        if level in Colors.LEVEL_COLORS:
            return Colors.LEVEL_COLORS[level] + text + Colors.RESET
        
        # Apply custom highlight rules
        result = text
        for rule in sorted(self.highlight_rules, key=lambda r: r.priority):
            result = rule.pattern.sub(
                lambda m: rule.color + m.group() + Colors.RESET,
                result
            )
        
        return result
    
    def _format_output(self, entry: LogEntry, source_width: int = 0) -> str:
        """Format log entry for display."""
        parts = []
        
        # Source file name
        if len(self.files) > 1:
            source = entry.source[-source_width:] if len(entry.source) > source_width else entry.source
            parts.append(f"[{source:>{source_width}}]")
        
        # Timestamp
        if self.show_timestamp:
            parts.append(f"{entry.timestamp}")
        
        # Level
        level_str = f"[{entry.level:8}]"
        if not self.no_color:
            level_str = self._colorize(level_str, entry.level)
        parts.append(level_str)
        
        # Message
        message = entry.message
        if not self.no_color:
            message = self._colorize(message)
        parts.append(message)
        
        return ' '.join(parts)
    
    def _print(self, text: str, raw: str = None):
        """Print to stdout and optionally save to file."""
        print(text)
        if self.output_file and raw:
            self.output_file.write(raw + '\n')
            self.output_file.flush()
    
    def _update_stats(self, entry: LogEntry, matched: bool):
        """Update statistics."""
        self.stats['total_lines'] += 1
        
        if matched:
            self.stats['matched_lines'] += 1
        
        # By level
        level = entry.level
        self.stats['by_level'][level] = self.stats['by_level'].get(level, 0) + 1
        
        # By file
        source = entry.source or 'unknown'
        if source not in self.stats['by_file']:
            self.stats['by_file'][source] = {'total': 0, 'matched': 0}
        self.stats['by_file'][source]['total'] += 1
        if matched:
            self.stats['by_file'][source]['matched'] += 1
    
    def _print_stats(self):
        """Print statistics."""
        if not self.stats_enabled:
            return
        
        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
        rate = self.stats['total_lines'] / elapsed if elapsed > 0 else 0
        
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}Statistics:{Colors.RESET}")
        print(f"  Runtime: {elapsed:.1f}s")
        print(f"  Total lines: {self.stats['total_lines']}")
        print(f"  Matched: {self.stats['matched_lines']}")
        print(f"  Rate: {rate:.1f} lines/sec")
        print(f"\n  By Level:")
        for level, count in sorted(self.stats['by_level'].items(), 
                                   key=lambda x: x[1], reverse=True)[:10]:
            pct = (count / self.stats['total_lines']) * 100
            print(f"    {level:12} {count:8} ({pct:5.1f}%)")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")
    
    def _read_file_from_position(self, filepath: str) -> List[str]:
        """Read new lines from file since last position."""
        lines = []
        try:
            with open(filepath, 'r', errors='replace') as f:
                # Seek to last position
                pos = self.file_positions.get(filepath, 0)
                f.seek(pos)
                
                # Read new lines
                lines = f.readlines()
                
                # Update position
                self.file_positions[filepath] = f.tell()
        except (IOError, OSError) as e:
            pass
        
        return lines
    
    def _check_rotation(self, filepath: str) -> bool:
        """Check if log file has been rotated."""
        try:
            current_inode = os.stat(filepath).st_ino
            previous_inode = self.inode_map.get(filepath)
            
            if previous_inode and previous_inode != current_inode:
                # File rotated, reset position
                self.file_positions[filepath] = 0
                self.inode_map[filepath] = current_inode
                return True
            
            self.inode_map[filepath] = current_inode
            return False
        except (IOError, OSError):
            return False
    
    def _tail_initial(self):
        """Show last N lines from each file."""
        for filepath in self.files:
            if not os.path.exists(filepath):
                print(f"Warning: File not found: {filepath}", file=sys.stderr)
                continue
            
            try:
                with open(filepath, 'r', errors='replace') as f:
                    # Seek to end
                    f.seek(0, 2)
                    end_pos = f.tell()
                    
                    # Read last N lines
                    lines = []
                    buffer_size = 8192
                    pos = end_pos
                    
                    while len(lines) < self.lines and pos > 0:
                        pos = max(0, pos - buffer_size)
                        f.seek(pos)
                        chunk = f.read(min(buffer_size, end_pos - pos))
                        lines = chunk.split('\n')
                        
                        if len(lines) > self.lines:
                            lines = lines[-self.lines:]
                    
                    # Store position for follow mode
                    self.file_positions[filepath] = end_pos
                    self.inode_map[filepath] = os.stat(filepath).st_ino
                    
                    # Display lines
                    source_name = os.path.basename(filepath)
                    max_source_len = max(len(os.path.basename(f)) for f in self.files)
                    
                    for line in lines:
                        if line:
                            entry = self._parse_log_line(line, source_name)
                            if self._should_display(entry):
                                output = self._format_output(entry, max_source_len)
                                self._print(output, entry.raw)
                            self._update_stats(entry, self._should_display(entry))
                            
            except (IOError, OSError) as e:
                print(f"Error reading {filepath}: {e}", file=sys.stderr)
    
    def _follow_files(self):
        """Follow files for new content."""
        print(f"\n{Colors.DIM}Following {len(self.files)} file(s)... Press Ctrl+C to exit{Colors.RESET}\n")
        
        max_source_len = max(len(os.path.basename(f)) for f in self.files) if self.files else 0
        
        while self.running:
            new_content = False
            
            for filepath in self.files:
                if not os.path.exists(filepath):
                    continue
                
                # Check for log rotation
                self._check_rotation(filepath)
                
                # Read new lines
                lines = self._read_file_from_position(filepath)
                
                if lines:
                    new_content = True
                    source_name = os.path.basename(filepath)
                    
                    for line in lines:
                        line = line.rstrip('\n')
                        if line:
                            entry = self._parse_log_line(line, source_name)
                            matched = self._should_display(entry)
                            self._update_stats(entry, matched)
                            
                            if matched:
                                output = self._format_output(entry, max_source_len)
                                self._print(output, entry.raw)
            
            if not new_content:
                # Small delay to prevent busy-waiting
                import time
                time.sleep(0.1)
    
    def run(self):
        """Main entry point."""
        # Setup signal handler
        def signal_handler(signum, frame):
            self.running = False
            if self.stats_enabled:
                self._print_stats()
            if self.output_file:
                self.output_file.close()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Print header
        print(f"{Colors.BOLD}Log Monitor{Colors.RESET}")
        print(f"Files: {', '.join(self.files)}")
        if self.include_filters:
            print(f"Include: {[p.pattern for p in self.include_filters]}")
        if self.exclude_filters:
            print(f"Exclude: {[p.pattern for p in self.exclude_filters]}")
        print()
        
        # Show initial lines
        self._tail_initial()
        
        # Follow mode
        if self.follow:
            try:
                self._follow_files()
            except KeyboardInterrupt:
                pass
            finally:
                if self.stats_enabled:
                    self._print_stats()
                if self.output_file:
                    self.output_file.close()


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description='Log Monitor - Real-time log tailing with filtering',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /var/log/syslog                          # Tail syslog
  %(prog)s -f app.log -f error.log                  # Monitor multiple files
  %(prog)s -n 50 /var/log/nginx/access.log          # Show last 50 lines
  %(prog)s -i "ERROR|FATAL" app.log                 # Show only errors
  %(prog)s -e "DEBUG|TRACE" app.log                 # Exclude debug messages
  %(prog)s -i "payment" -i "failed" app.log         # Multiple include filters
  %(prog)s --highlight "exception:red" app.log      # Custom highlighting
  %(prog)s -s app.log                               # Show statistics on exit
  %(prog)s --save output.log app.log                # Save matched lines
        """
    )
    
    parser.add_argument('files', nargs='*', help='Log files to monitor')
    parser.add_argument('-f', '--file', action='append', dest='file_args',
                        help='Log file to monitor (can be used multiple times)')
    parser.add_argument('-n', '--lines', type=int, default=10,
                        help='Number of initial lines to show (default: 10)')
    parser.add_argument('-F', '--no-follow', action='store_true',
                        help='Do not follow file, exit after initial lines')
    parser.add_argument('-i', '--include', action='append',
                        help='Include pattern (regex)')
    parser.add_argument('-e', '--exclude', action='append',
                        help='Exclude pattern (regex)')
    parser.add_argument('-c', '--case-sensitive', action='store_true',
                        help='Case-sensitive matching')
    parser.add_argument('--fixed-strings', action='store_true',
                        help='Treat patterns as fixed strings, not regex')
    parser.add_argument('--no-color', action='store_true',
                        help='Disable color output')
    parser.add_argument('-t', '--timestamp', action='store_true',
                        help='Show timestamp prefix')
    parser.add_argument('--highlight', action='append',
                        help='Custom highlight: pattern:color (e.g., "error:red")')
    parser.add_argument('-s', '--stats', action='store_true',
                        help='Show statistics on exit')
    parser.add_argument('--save', metavar='FILE',
                        help='Save matched lines to file')
    
    args = parser.parse_args()
    
    # Collect files
    files = args.files or []
    if args.file_args:
        files.extend(args.file_args)
    
    if not files:
        parser.error("No log files specified")
    
    # Validate files
    valid_files = []
    for f in files:
        if os.path.exists(f):
            valid_files.append(f)
        else:
            print(f"Warning: File not found: {f}", file=sys.stderr)
    
    if not valid_files:
        parser.error("No valid log files found")
    
    monitor = LogMonitor(
        files=valid_files,
        follow=not args.no_follow,
        lines=args.lines,
        include_patterns=args.include,
        exclude_patterns=args.exclude,
        case_sensitive=args.case_sensitive,
        regex=not args.fixed_strings,
        no_color=args.no_color,
        timestamp=args.timestamp,
        highlight_rules=args.highlight,
        stats=args.stats,
        save_to=args.save,
    )
    
    monitor.run()


if __name__ == '__main__':
    main()
