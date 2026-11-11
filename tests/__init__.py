import sys
from pathlib import Path

# The scripts under test are plain files in scripts/, not an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
