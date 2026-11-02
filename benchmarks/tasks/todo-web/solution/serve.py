"""Reference solution: serve the to-do app's files from this folder."""

import argparse
import functools
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    handler = functools.partial(Quiet, directory=os.path.dirname(os.path.abspath(__file__)))
    ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
