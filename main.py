"""AutoBot RPA — entry point."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ui.ai_app import AIBotApp


def main() -> None:
    app = AIBotApp()
    app.mainloop()


if __name__ == '__main__':
    main()
