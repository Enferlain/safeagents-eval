"""Publication boundaries and the dependency-free test entry point."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_retained_upstream_license_bytes_match_receipts(self):
        receipts = json.loads((ROOT / 'licenses/SOURCES.json').read_text())
        self.assertTrue(receipts)
        for receipt in receipts:
            with self.subTest(path=receipt['path']):
                actual = hashlib.sha256((ROOT / receipt['path']).read_bytes()).hexdigest()
                self.assertEqual(actual, receipt['sha256'])

    def test_ci_uses_the_same_locked_check_command_and_commit_pinned_actions(self):
        workflow = ROOT / '.github/workflows/ci.yml'
        self.assertTrue(workflow.is_file(), 'CI workflow is missing')
        text = workflow.read_text()
        self.assertIn('run: uv run --locked python scripts/check.py', text)
        self.assertIn('persist-credentials: false', text)
        self.assertNotIn('pull_request_target', text)
        actions = re.findall(r'uses:\s+([^\s#]+)', text)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r'[^@]+@[0-9a-f]{40}', action) for action in actions))

    def test_readme_relative_links_exist(self):
        text = (ROOT / 'README.md').read_text()
        links = re.findall(r'\]\(([^)]+)\)', text)
        missing = [link for link in links if not link.startswith(('https://', 'http://', '#'))
                   and not (ROOT / link.split('#', 1)[0]).is_file()]
        self.assertEqual(missing, [], 'README has missing local targets')

    def test_default_checks_are_offline_and_do_not_select_reference_or_gpu_runs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/check.py'), '--list'],
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(set(plan['default_suites']), {'core', 'trial', 'page', 'xaab', 'repository', 'ui'})
        self.assertNotIn('references', plan['default_suites'])
        self.assertNotIn('cuda', plan['default_suites'])
        self.assertTrue(all('run_model.py' not in argument and 'launch.sh' not in argument
                            for command in plan['commands'].values() for argument in command))

    def test_git_boundary_keeps_local_data_secrets_and_vendor_downloads_out(self):
        excluded = [
            '.env', '.env.local', '.secrets', '.venv/bin/python', '.venv-trial/bin/python',
            '.venv-checks/bin/python', '.uv-cache-win/cache', '__pycache__/run.pyc',
            'dataset.json', 'metadata.json', 'summary.json', 'powershell-output.txt',
            'results/model/predictions.jsonl', 'references/Intern-Decision-0.8B/inference.py',
            'page/data/LICENSE.md', 'page/data/cases.json', 'page/README.md',
            'page/COMPARISON.md', 'page/lite-001/summary.json',
            'trial/upstream/.git/config', 'trial/journal.jsonl', 'trial/local-journal.jsonl',
            'trial/private-observation/calls.jsonl', 'trial/private-observation/accident.py',
            'trial/observation-verification/calls.jsonl', 'trial/logs/judge.log',
            'trial/judge_adapter.py.pre-observation', 'trial/verify-hermes-routes.json',
            'trial/security-plugin/.env', 'trial/security-plugin/dashboard/unreviewed.json',
            'xaab/data/scenarios/secret.yaml', 'xaab/run-001/results.jsonl',
            'unreviewed-folder/private.txt', 'unexpected.py', 'trial/unreviewed.py',
            'docs/unreviewed.md', 'id_rsa', 'key.pem',
        ]
        included = [
            '.gitignore', '.gitattributes', 'README.md', 'LICENSE', 'NOTICE',
            'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', 'pyproject.toml', 'uv.lock',
            'licenses/page/LICENSE.txt', 'references/sources.json',
            'run.py', 'analyze.py', 'test_metrics.py', 'REPORT.md', 'dataset-provenance.json',
            'scripts/check.py', 'tests/test_repository.py', '.github/workflows/ci.yml',
            'docs/DEVELOPMENT.md', 'docs/BENCHMARKS.md', 'page/runner.py', 'page/test_runner.py',
            'trial/decision_models.py', 'trial/test_security_ui.mjs', 'trial/OBSERVATION.md',
            'trial/security-plugin/service.py', 'trial/security-plugin/plugin.yaml',
            'trial/security-plugin/dashboard/plugin_api.py',
            'trial/security-plugin/dashboard/manifest.json',
            'trial/security-plugin/desktop/plugin.js', 'xaab/runner.py', 'xaab/README.md',
        ]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            home = temporary / 'home'
            home.mkdir()
            checkout = temporary / 'checkout'
            checkout.mkdir()
            shutil.copyfile(ROOT / '.gitignore', checkout / '.gitignore')
            for name in excluded + included:
                (checkout / name).parent.mkdir(parents=True, exist_ok=True)
            environment = dict(os.environ, HOME=str(home), GIT_CONFIG_GLOBAL=os.devnull,
                               GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM='1')
            subprocess.run(['git', '-c', 'init.defaultBranch=main', '-c', 'init.templateDir=',
                            'init', '--quiet'], cwd=checkout, env=environment, check=True,
                           capture_output=True, timeout=10)
            result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin'],
                                    input='\n'.join(excluded + included) + '\n',
                                    cwd=checkout, env=environment, capture_output=True,
                                    text=True, timeout=10, check=False)
            self.assertIn(result.returncode, (0, 1), result.stderr)
            ignored = set(result.stdout.splitlines())
            self.assertEqual(set(excluded) - ignored, set(), 'local/private artifacts are publishable')
            self.assertEqual(set(included) & ignored, set(), 'maintained project files are hidden')


if __name__ == '__main__':
    unittest.main()
