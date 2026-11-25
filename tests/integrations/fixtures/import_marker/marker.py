"""TEST FIXTURE: writes IMPORTED next to itself when imported (proves discovery never imports)."""
import os
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'IMPORTED'), 'w').write('x')

def run(payload, ctx):
    return {'summary': 'x', 'words': 1}
