"""Reset a stuck Windows loading cursor. Run: python fix_cursor.py"""
from control import restore_pointer

restore_pointer()
print("Cursor reset to the normal arrow.")
