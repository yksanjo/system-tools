#!/usr/bin/env python3
"""
Disk Analyzer - Visual disk usage analyzer with interactive navigation.
Similar to 'ncdu' or 'du' but with a terminal UI for exploring disk usage.
"""

import os
import sys
import curses
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable
from dataclasses import dataclass, field
from collections import defaultdict
import threading
import time


@dataclass
class FileNode:
    """Represents a file or directory with size information."""
    path: Path
    name: str
    is_dir: bool
    size: int = 0
    children: List['FileNode'] = field(default_factory=list)
    parent: Optional['FileNode'] = None
    error: bool = False
    
    def __post_init__(self):
        if not self.is_dir:
            try:
                self.size = self.path.stat().st_size
            except (OSError, PermissionError):
                self.error = True
                self.size = 0
    
    @property
    def size_str(self) -> str:
        """Human-readable size."""
        return format_size(self.size)
    
    @property
    def percent_of_parent(self) -> float:
        """Calculate percentage of parent's size."""
        if self.parent and self.parent.size > 0:
            return (self.size / self.parent.size) * 100
        return 0.0


def format_size(size: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ['B', 'K', 'M', 'G', 'T', 'P']:
        if abs(size) < 1024:
            if unit == 'B':
                return f"{size}B"
            return f"{size:5.1f}{unit}"
        size /= 1024
    return f"{size:5.1f}E"


def draw_bar(percent: float, width: int = 20) -> str:
    """Draw ASCII progress bar."""
    filled = int(width * percent / 100)
    return '█' * filled + '░' * (width - filled)


class DiskScanner:
    """Scans directories and builds size tree."""
    
    def __init__(self, 
                 exclude_patterns: List[str] = None,
                 follow_symlinks: bool = False,
                 one_filesystem: bool = False,
                 progress_callback: Callable = None):
        self.exclude_patterns = exclude_patterns or []
        self.follow_symlinks = follow_symlinks
        self.one_filesystem = one_filesystem
        self.progress_callback = progress_callback
        self.scanned_count = 0
        self.root_device = None
        
    def should_exclude(self, path: Path) -> bool:
        """Check if path should be excluded."""
        path_str = str(path)
        for pattern in self.exclude_patterns:
            if pattern in path_str:
                return True
        return False
    
    def scan(self, path: Path) -> FileNode:
        """Scan directory and build tree."""
        path = path.resolve()
        
        # Get root device for one-filesystem option
        if self.one_filesystem:
            try:
                self.root_device = os.stat(path).st_dev
            except OSError:
                pass
        
        return self._scan_recursive(path, None)
    
    def _scan_recursive(self, path: Path, parent: Optional[FileNode]) -> FileNode:
        """Recursively scan directory."""
        is_dir = path.is_dir()
        
        node = FileNode(
            path=path,
            name=path.name or str(path),
            is_dir=is_dir,
            parent=parent
        )
        
        if self.should_exclude(path):
            return node
        
        if is_dir:
            try:
                # Check filesystem boundary
                if self.one_filesystem and self.root_device is not None:
                    try:
                        if os.stat(path).st_dev != self.root_device:
                            node.error = True
                            return node
                    except OSError:
                        pass
                
                entries = list(os.scandir(path))
                
                for entry in entries:
                    if not self.follow_symlinks and entry.is_symlink():
                        continue
                    
                    child = self._scan_recursive(Path(entry.path), node)
                    node.children.append(child)
                    node.size += child.size
                    
                    self.scanned_count += 1
                    if self.progress_callback and self.scanned_count % 100 == 0:
                        self.progress_callback(self.scanned_count)
                        
            except PermissionError:
                node.error = True
            except OSError:
                node.error = True
        
        # Sort children by size (descending)
        node.children.sort(key=lambda x: x.size, reverse=True)
        
        return node


class DiskAnalyzerUI:
    """Interactive curses UI for disk analyzer."""
    
    def __init__(self, root: FileNode, show_hidden: bool = False):
        self.root = root
        self.current_node = root
        self.show_hidden = show_hidden
        
        self.scroll_top = 0
        self.cursor_pos = 0
        self.visible_items: List[FileNode] = []
        
        self.sort_by = 'size'  # size, name, count
        self.sort_reverse = False
        
    def get_visible_children(self) -> List[FileNode]:
        """Get filtered and sorted children."""
        children = self.current_node.children
        
        if not self.show_hidden:
            children = [c for c in children if not c.name.startswith('.')]
        
        if self.sort_by == 'size':
            children = sorted(children, key=lambda x: x.size, reverse=not self.sort_reverse)
        elif self.sort_by == 'name':
            children = sorted(children, key=lambda x: x.name.lower(), 
                            reverse=self.sort_reverse)
        elif self.sort_by == 'count':
            children = sorted(children, 
                            key=lambda x: len(x.children) if x.is_dir else 0,
                            reverse=not self.sort_reverse)
        
        return children
    
    def draw(self, stdscr):
        """Main draw function."""
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()
        
        # Header
        header = f" Disk Analyzer - {self.current_node.path} "
        stdscr.addstr(0, 0, header[:max_x-1], curses.A_BOLD | curses.A_REVERSE)
        
        # Info bar
        info = f" Total: {self.current_node.size_str} | Items: {len(self.current_node.children)} "
        stdscr.addstr(1, 0, info[:max_x-1], curses.A_DIM)
        
        # Column headers
        header_y = 2
        col_size = 10
        col_percent = 22
        col_name = max_x - col_size - col_percent - 5
        
        headers = f"{'Size':>{col_size}} {'Graph':^{col_percent}} {'Name':<{col_name}}"
        stdscr.addstr(header_y, 0, headers, curses.A_BOLD | curses.A_UNDERLINE)
        
        # Get visible items
        self.visible_items = self.get_visible_children()
        
        # Ensure cursor in bounds
        if self.cursor_pos >= len(self.visible_items):
            self.cursor_pos = max(0, len(self.visible_items) - 1)
        
        # Calculate scroll
        content_height = max_y - 5
        if self.cursor_pos < self.scroll_top:
            self.scroll_top = self.cursor_pos
        elif self.cursor_pos >= self.scroll_top + content_height:
            self.scroll_top = self.cursor_pos - content_height + 1
        
        # Draw items
        for i, item in enumerate(self.visible_items[self.scroll_top:self.scroll_top + content_height]):
            row = header_y + 1 + i
            if row >= max_y - 2:
                break
            
            # Format columns
            size_str = item.size_str
            percent = item.percent_of_parent
            bar = draw_bar(percent, col_percent - 6)
            name = item.name[:col_name-2]
            
            if item.is_dir:
                name = f"📁 {name}/"
            else:
                name = f"📄 {name}"
            
            if item.error:
                name += " [E]"
            
            line = f"{size_str:>{col_size}} {bar} {percent:5.1f}% {name}"
            
            # Highlight cursor
            attrs = 0
            if self.scroll_top + i == self.cursor_pos:
                attrs = curses.A_REVERSE
            
            # Color directories
            if item.is_dir and not (attrs & curses.A_REVERSE):
                attrs |= curses.A_BOLD
            
            try:
                stdscr.addstr(row, 0, line[:max_x-1], attrs)
            except curses.error:
                pass
        
        # Footer with help
        footer_y = max_y - 2
        help_text = " ↑/↓:Navigate  Enter:Open  ←/q:Back  d:Delete  s:Sort  h:Hidden  ?:Help "
        stdscr.addstr(footer_y, 0, help_text[:max_x-1], curses.A_DIM | curses.A_REVERSE)
        
        stdscr.refresh()
    
    def run(self, stdscr):
        """Main UI loop."""
        curses.curs_set(0)
        stdscr.nodelay(0)
        
        # Enable colors
        curses.start_color()
        curses.use_default_colors()
        
        while True:
            self.draw(stdscr)
            
            key = stdscr.getch()
            
            if key == ord('q') or key == ord('Q'):
                break
            elif key == curses.KEY_UP or key == ord('k'):
                self.cursor_pos = max(0, self.cursor_pos - 1)
            elif key == curses.KEY_DOWN or key == ord('j'):
                self.cursor_pos = min(len(self.visible_items) - 1, self.cursor_pos + 1)
            elif key == curses.KEY_PPAGE:
                self.cursor_pos = max(0, self.cursor_pos - 10)
            elif key == curses.KEY_NPAGE:
                self.cursor_pos = min(len(self.visible_items) - 1, self.cursor_pos + 10)
            elif key == curses.KEY_HOME:
                self.cursor_pos = 0
            elif key == curses.KEY_END:
                self.cursor_pos = len(self.visible_items) - 1
            elif key == ord('\n') or key == curses.KEY_RIGHT:
                # Enter directory
                if self.visible_items and self.cursor_pos < len(self.visible_items):
                    selected = self.visible_items[self.cursor_pos]
                    if selected.is_dir and selected.children:
                        self.current_node = selected
                        self.cursor_pos = 0
                        self.scroll_top = 0
            elif key == curses.KEY_LEFT or key == curses.KEY_BACKSPACE:
                # Go up
                if self.current_node.parent:
                    # Find position of current node in parent
                    self.cursor_pos = self.current_node.parent.children.index(self.current_node)
                    self.current_node = self.current_node.parent
                    self.scroll_top = max(0, self.cursor_pos - 5)
            elif key == ord('h') or key == ord('H'):
                self.show_hidden = not self.show_hidden
                self.cursor_pos = 0
                self.scroll_top = 0
            elif key == ord('s') or key == ord('S'):
                # Cycle sort modes
                modes = ['size', 'name', 'count']
                idx = modes.index(self.sort_by)
                self.sort_by = modes[(idx + 1) % len(modes)]
            elif key == ord('r') or key == ord('R'):
                self.sort_reverse = not self.sort_reverse
            elif key == ord('d') or key == ord('D'):
                # Delete confirmation
                if self.visible_items and self.cursor_pos < len(self.visible_items):
                    selected = self.visible_items[self.cursor_pos]
                    if self._confirm_delete(stdscr, selected):
                        self._delete_item(selected)
            elif key == ord('?'):
                self._show_help(stdscr)
    
    def _confirm_delete(self, stdscr, item: FileNode) -> bool:
        """Show delete confirmation dialog."""
        max_y, max_x = stdscr.getmaxyx()
        
        dialog = [
            "Delete this item?",
            f"{item.name} ({item.size_str})",
            "",
            "Press Y to confirm, any other key to cancel"
        ]
        
        start_y = (max_y - len(dialog)) // 2
        start_x = (max_x - max(len(l) for l in dialog)) // 2
        
        for i, line in enumerate(dialog):
            try:
                stdscr.addstr(start_y + i, start_x, line, curses.A_BOLD)
            except:
                pass
        
        stdscr.refresh()
        
        key = stdscr.getch()
        return key == ord('y') or key == ord('Y')
    
    def _delete_item(self, item: FileNode):
        """Delete file or directory."""
        try:
            if item.is_dir:
                import shutil
                shutil.rmtree(item.path)
            else:
                item.path.unlink()
            
            # Refresh parent
            if item.parent:
                item.parent.children.remove(item)
                item.parent.size -= item.size
        except (OSError, PermissionError) as e:
            pass  # Could show error
    
    def _show_help(self, stdscr):
        """Show help dialog."""
        max_y, max_x = stdscr.getmaxyx()
        
        help_text = [
            "Disk Analyzer Help",
            "",
            "Navigation:",
            "  ↑/↓, j/k    Move cursor",
            "  PgUp/PgDn   Move 10 items",
            "  Home/End    Jump to first/last",
            "  Enter/→     Open directory",
            "  ←/Backspace Go to parent",
            "",
            "Actions:",
            "  h           Toggle hidden files",
            "  s           Change sort order",
            "  r           Reverse sort",
            "  d           Delete selected",
            "  ?           Show this help",
            "  q           Quit",
            "",
            "Press any key to continue..."
        ]
        
        # Draw box
        box_height = len(help_text) + 2
        box_width = max(len(l) for l in help_text) + 4
        start_y = (max_y - box_height) // 2
        start_x = (max_x - box_width) // 2
        
        for y in range(box_height):
            for x in range(box_width):
                stdscr.addch(start_y + y, start_x + x, ' ', curses.A_REVERSE)
        
        # Draw text
        for i, line in enumerate(help_text):
            attr = curses.A_BOLD if i == 0 else 0
            try:
                stdscr.addstr(start_y + 1 + i, start_x + 2, line, attr)
            except:
                pass
        
        stdscr.refresh()
        stdscr.getch()


def scan_with_progress(path: Path, exclude: List[str]) -> FileNode:
    """Scan with progress display."""
    print(f"Scanning {path}...")
    print("(This may take a while for large directories)")
    
    scanner = DiskScanner(
        exclude_patterns=exclude,
        progress_callback=lambda n: print(f"  Scanned {n} items...", end='\r', flush=True)
    )
    
    root = scanner.scan(path)
    print(f"\n  Complete! Scanned {scanner.scanned_count} items.")
    print(f"  Total size: {format_size(root.size)}")
    time.sleep(1)
    
    return root


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Disk Analyzer - Visual disk usage analyzer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /home/user              # Analyze home directory
  %(prog)s -x node_modules .git    # Exclude patterns
  %(prog)s -a /var/log             # Show hidden files
  %(prog)s -x                      # Use current directory
        """
    )
    
    parser.add_argument('path', nargs='?', default='.',
                        help='Directory to analyze (default: current directory)')
    parser.add_argument('-x', '--exclude', action='append', default=[],
                        help='Exclude pattern (can be used multiple times)')
    parser.add_argument('-a', '--all', action='store_true',
                        help='Show hidden files')
    parser.add_argument('-L', '--follow-symlinks', action='store_true',
                        help='Follow symbolic links')
    parser.add_argument('-X', '--one-filesystem', action='store_true',
                        help='Stay on one filesystem')
    
    args = parser.parse_args()
    
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"Error: Path does not exist: {path}", file=sys.stderr)
        return 1
    
    if not path.is_dir():
        print(f"Error: Not a directory: {path}", file=sys.stderr)
        return 1
    
    # Default excludes
    exclude = args.exclude + ['.git', '__pycache__', '.cache', 'node_modules']
    
    # Scan
    root = scan_with_progress(path, exclude)
    
    # Launch UI
    ui = DiskAnalyzerUI(root, show_hidden=args.all)
    
    try:
        curses.wrapper(ui.run)
    except KeyboardInterrupt:
        pass
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
