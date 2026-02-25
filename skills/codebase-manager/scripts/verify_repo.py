#!/usr/bin/env python3
import os

def check_structure():
    required_dirs = ["nova", "nova/tools", "skills"]
    required_files = ["requirements.txt", "Dockerfile", "start.sh", "nova/agent.py"]
    
    missing = []
    for d in required_dirs:
        if not os.path.isdir(d):
            missing.append(f"Directory missing: {d}")
            
    for f in required_files:
        if not os.path.isfile(f):
            missing.append(f"File missing: {f}")
            
    if missing:
        print("\n".join(missing))
        return False
    
    print("Codebase structure is valid.")
    return True

if __name__ == "__main__":
    if not check_structure():
        exit(1)
