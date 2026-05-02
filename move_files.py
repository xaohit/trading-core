import shutil, os, sys

# Define file movements
moves = [
    ('risk.py', 'risk/'),
    ('executor.py', 'execution/'),
    ('decision_memory.py', 'memory/'),
    ('self_optimizer.py', 'learning/'),
    ('semantic_radar.py', 'radar/')
]

print("Starting file reorganization...")
for src, dst_dir in moves:
    if os.path.exists(src):
        # Ensure destination directory exists
        os.makedirs(dst_dir, exist_ok=True)
        dst_path = os.path.join(dst_dir, src)
        
        # Move file
        shutil.move(src, dst_path)
        print(f"Moved: {src} -> {dst_path}")
    else:
        print(f"Not found: {src}")

print("\n📂 Current structure:")
for d in ['risk', 'execution', 'memory', 'learning', 'radar']:
    if os.path.exists(d):
        print(f"  {d}/: {os.listdir(d)}")
