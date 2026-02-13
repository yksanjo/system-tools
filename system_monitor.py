#!/usr/bin/env python3
"""
System Monitor - Real-time CPU, Memory, and Disk usage monitor.
Learn about system calls and process monitoring.
"""

import os
import sys
import time
import curses
from datetime import datetime
from typing import Dict, List, Tuple, Optional


class SystemMonitor:
    """Cross-platform system monitor using /proc filesystem and system calls."""
    
    def __init__(self):
        self.page_size = os.sysconf(os.sysconf_names.get('SC_PAGE_SIZE', 4096))
        self.clk_tck = os.sysconf(os.sysconf_names.get('SC_CLK_TCK', 100))
        
    def read_proc_file(self, path: str) -> str:
        """Read a file from /proc filesystem."""
        try:
            with open(path, 'r') as f:
                return f.read()
        except (IOError, PermissionError) as e:
            return f"Error reading {path}: {e}"
    
    def get_cpu_stats(self) -> Dict:
        """Get CPU statistics from /proc/stat."""
        content = self.read_proc_file('/proc/stat')
        lines = content.strip().split('\n')
        
        cpu_stats = {}
        for line in lines:
            if line.startswith('cpu'):
                parts = line.split()
                name = parts[0]
                values = [int(x) for x in parts[1:] if x.isdigit()]
                cpu_stats[name] = {
                    'user': values[0] if len(values) > 0 else 0,
                    'nice': values[1] if len(values) > 1 else 0,
                    'system': values[2] if len(values) > 2 else 0,
                    'idle': values[3] if len(values) > 3 else 0,
                    'iowait': values[4] if len(values) > 4 else 0,
                    'irq': values[5] if len(values) > 5 else 0,
                    'softirq': values[6] if len(values) > 6 else 0,
                }
        return cpu_stats
    
    def calculate_cpu_percent(self, prev: Dict, curr: Dict) -> float:
        """Calculate CPU usage percentage between two readings."""
        prev_idle = prev['idle'] + prev['iowait']
        curr_idle = curr['idle'] + curr['iowait']
        
        prev_total = sum(prev.values())
        curr_total = sum(curr.values())
        
        total_diff = curr_total - prev_total
        idle_diff = curr_idle - prev_idle
        
        if total_diff == 0:
            return 0.0
        
        return ((total_diff - idle_diff) / total_diff) * 100
    
    def get_memory_stats(self) -> Dict:
        """Get memory statistics from /proc/meminfo."""
        content = self.read_proc_file('/proc/meminfo')
        stats = {}
        
        for line in content.strip().split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                # Extract numeric value (in kB)
                num = ''.join(filter(str.isdigit, value))
                stats[key.strip()] = int(num) if num else 0
        
        total = stats.get('MemTotal', 1)
        available = stats.get('MemAvailable', stats.get('MemFree', 0))
        used = total - available
        
        return {
            'total_kb': total,
            'available_kb': available,
            'used_kb': used,
            'percent': (used / total) * 100 if total > 0 else 0,
            'buffers_kb': stats.get('Buffers', 0),
            'cached_kb': stats.get('Cached', 0),
        }
    
    def get_disk_stats(self) -> List[Dict]:
        """Get disk usage statistics using statvfs."""
        mounts = []
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        device, mountpoint = parts[0], parts[1]
                        # Skip virtual filesystems
                        if device.startswith('/dev/') or mountpoint in ['/', '/home']:
                            try:
                                stat = os.statvfs(mountpoint)
                                total = stat.f_blocks * stat.f_frsize
                                free = stat.f_bfree * stat.f_frsize
                                available = stat.f_bavail * stat.f_frsize
                                used = total - free
                                
                                mounts.append({
                                    'device': device,
                                    'mountpoint': mountpoint,
                                    'total_bytes': total,
                                    'used_bytes': used,
                                    'available_bytes': available,
                                    'percent': (used / total) * 100 if total > 0 else 0,
                                })
                            except OSError:
                                pass
        except IOError:
            pass
        return mounts
    
    def get_processes(self, limit: int = 10) -> List[Dict]:
        """Get top processes by CPU usage."""
        processes = []
        
        try:
            for pid in os.listdir('/proc'):
                if pid.isdigit():
                    try:
                        # Read process status
                        status = self.read_proc_file(f'/proc/{pid}/status')
                        stat = self.read_proc_file(f'/proc/{pid}/stat')
                        
                        name = "unknown"
                        for line in status.split('\n'):
                            if line.startswith('Name:'):
                                name = line.split(':', 1)[1].strip()
                            elif line.startswith('VmRSS:'):
                                mem_kb = int(''.join(filter(str.isdigit, line.split(':', 1)[1])))
                                break
                        else:
                            mem_kb = 0
                        
                        # Parse stat file for CPU time
                        stat_parts = stat.split()
                        if len(stat_parts) > 13:
                            utime = int(stat_parts[13])
                            stime = int(stat_parts[14]) if len(stat_parts) > 14 else 0
                            cpu_time = (utime + stime) / self.clk_tck
                        else:
                            cpu_time = 0
                        
                        processes.append({
                            'pid': int(pid),
                            'name': name,
                            'mem_kb': mem_kb,
                            'cpu_time': cpu_time,
                        })
                    except (ValueError, IndexError):
                        continue
        except PermissionError:
            pass
        
        # Sort by memory usage (could also sort by CPU)
        processes.sort(key=lambda x: x['mem_kb'], reverse=True)
        return processes[:limit]
    
    def format_bytes(self, bytes_val: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} PB"
    
    def draw_bar(self, percent: float, width: int = 30) -> str:
        """Draw an ASCII progress bar."""
        filled = int(width * percent / 100)
        bar = '█' * filled + '░' * (width - filled)
        return f"[{bar}] {percent:.1f}%"


def run_monitor(stdscr):
    """Main curses application for system monitor."""
    curses.curs_set(0)  # Hide cursor
    stdscr.nodelay(1)   # Non-blocking input
    stdscr.timeout(1000)  # Refresh every second
    
    monitor = SystemMonitor()
    prev_cpu_stats = monitor.get_cpu_stats()
    
    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()
        
        # Title
        title = " SYSTEM MONITOR - Press 'q' to quit "
        stdscr.addstr(0, (max_x - len(title)) // 2, title, curses.A_BOLD | curses.A_REVERSE)
        stdscr.addstr(1, 0, f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        row = 3
        
        # CPU Section
        stdscr.addstr(row, 0, "┌" + "─" * 48 + "┐", curses.A_BOLD)
        row += 1
        stdscr.addstr(row, 0, "│" + " CPU USAGE ".center(48) + "│", curses.A_BOLD)
        row += 1
        stdscr.addstr(row, 0, "├" + "─" * 48 + "┤", curses.A_BOLD)
        row += 1
        
        curr_cpu_stats = monitor.get_cpu_stats()
        if 'cpu' in curr_cpu_stats and 'cpu' in prev_cpu_stats:
            cpu_percent = monitor.calculate_cpu_percent(prev_cpu_stats['cpu'], curr_cpu_stats['cpu'])
            bar = monitor.draw_bar(cpu_percent, 40)
            stdscr.addstr(row, 0, f"│ Total: {bar} │", curses.A_BOLD)
            row += 1
            
            # Per-core stats
            core_row = row
            for i in range(os.cpu_count() or 1):
                core_key = f'cpu{i}'
                if core_key in curr_cpu_stats and core_key in prev_cpu_stats:
                    core_percent = monitor.calculate_cpu_percent(
                        prev_cpu_stats[core_key], curr_cpu_stats[core_key]
                    )
                    stdscr.addstr(core_row, 0, f"│ Core {i:2d}: {monitor.draw_bar(core_percent, 36)} │")
                    core_row += 1
            row = core_row
        
        stdscr.addstr(row, 0, "└" + "─" * 48 + "┘", curses.A_BOLD)
        row += 2
        
        # Memory Section
        stdscr.addstr(row, 0, "┌" + "─" * 48 + "┐", curses.A_BOLD)
        row += 1
        stdscr.addstr(row, 0, "│" + " MEMORY ".center(48) + "│", curses.A_BOLD)
        row += 1
        stdscr.addstr(row, 0, "├" + "─" * 48 + "┤", curses.A_BOLD)
        row += 1
        
        mem_stats = monitor.get_memory_stats()
        mem_bar = monitor.draw_bar(mem_stats['percent'], 40)
        stdscr.addstr(row, 0, f"│ Used:  {mem_bar} │")
        row += 1
        stdscr.addstr(row, 0, f"│ Total: {monitor.format_bytes(mem_stats['total_kb'] * 1024):>10}  "
                               f"Used: {monitor.format_bytes(mem_stats['used_kb'] * 1024):>10}  "
                               f"Free: {monitor.format_bytes(mem_stats['available_kb'] * 1024):>10} │")
        row += 1
        stdscr.addstr(row, 0, "└" + "─" * 48 + "┘", curses.A_BOLD)
        row += 2
        
        # Disk Section
        stdscr.addstr(row, 0, "┌" + "─" * 68 + "┐", curses.A_BOLD)
        row += 1
        stdscr.addstr(row, 0, "│" + " DISK USAGE ".center(68) + "│", curses.A_BOLD)
        row += 1
        stdscr.addstr(row, 0, "├" + "─" * 68 + "┤", curses.A_BOLD)
        row += 1
        
        disk_stats = monitor.get_disk_stats()
        for disk in disk_stats[:4]:  # Show up to 4 disks
            disk_bar = monitor.draw_bar(disk['percent'], 30)
            mount = disk['mountpoint'][:15]
            stdscr.addstr(row, 0, f"│ {mount:15s} {disk_bar} "
                                   f"{monitor.format_bytes(disk['used_bytes']):>10}/{monitor.format_bytes(disk['total_bytes']):>10} │")
            row += 1
        
        stdscr.addstr(row, 0, "└" + "─" * 68 + "┘", curses.A_BOLD)
        row += 2
        
        # Top Processes
        if row + 5 < max_y:
            stdscr.addstr(row, 0, "┌" + "─" * 58 + "┐", curses.A_BOLD)
            row += 1
            stdscr.addstr(row, 0, "│" + " TOP PROCESSES (by Memory) ".center(58) + "│", curses.A_BOLD)
            row += 1
            stdscr.addstr(row, 0, "├" + "─" * 58 + "┤", curses.A_BOLD)
            row += 1
            stdscr.addstr(row, 0, f"│ {'PID':>8} {'Name':<20} {'Memory':>12} {'CPU Time':>12} │")
            row += 1
            
            processes = monitor.get_processes(8)
            for proc in processes:
                if row < max_y - 1:
                    mem_str = monitor.format_bytes(proc['mem_kb'] * 1024)
                    stdscr.addstr(row, 0, f"│ {proc['pid']:>8} {proc['name'][:20]:<20} "
                                           f"{mem_str:>12} {proc['cpu_time']:>11.1f}s │")
                    row += 1
            
            stdscr.addstr(row, 0, "└" + "─" * 58 + "┘", curses.A_BOLD)
        
        stdscr.refresh()
        prev_cpu_stats = curr_cpu_stats
        
        # Check for quit
        key = stdscr.getch()
        if key == ord('q') or key == ord('Q'):
            break


def main():
    """Entry point for system monitor."""
    if sys.platform != 'linux':
        print("Warning: This tool is optimized for Linux systems.")
        print("Some features may not work correctly on other platforms.")
        time.sleep(2)
    
    try:
        curses.wrapper(run_monitor)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == '__main__':
    main()
