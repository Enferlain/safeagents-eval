"""Compatibility entrypoint: explicitly select QEV in the shared offline launcher."""
import sys

import replay_model


def main(argv=None, *, store_factory=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # The legacy command is always QEV, never an implicit model selector.
    if any(arg.startswith('--mo') for arg in args):
        print('error: ValueError')
        return 1
    return replay_model.main(['--model', 'qev', *args], store_factory=store_factory)


if __name__ == '__main__':
    raise SystemExit(main())
