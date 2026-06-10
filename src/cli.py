from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send World Cup betting match Telegram alerts.")
    parser.add_argument("--dry-run", action="store_true", help="Print the Telegram message without sending or saving state.")
    parser.add_argument("--lookahead-hours", type=float, help="How many hours ahead to search for matches.")
    parser.add_argument("--match-id", help="Run pipelines for a specific fixture id, ignoring the notification window.")
    parser.add_argument("--send-test-telegram", action="store_true", help="Send a small test Telegram message.")
    parser.add_argument("--manual-override", action="store_true", help="Ignore the 3-hour notification window.")
    parser.add_argument("--save-dry-run", action="store_true", help="Persist notification state even during dry-run.")
    return parser
