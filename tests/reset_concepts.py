"""重置概念：清空 concepts / concept_links，并把事件标记回 raw（用于重测巩固）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neuromem import db


def main():
    conn = db.connect()
    conn.execute("DELETE FROM concept_links")
    conn.execute("DELETE FROM concepts")
    conn.execute("UPDATE events SET consolidation_stage=?", ("raw",))
    conn.commit()
    print("cleaned concepts & links, events reset to raw")


if __name__ == "__main__":
    main()