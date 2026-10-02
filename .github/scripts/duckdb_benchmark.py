"""Measure CI phases on fresh runners, preserving unsuccessful results honestly."""

import glob
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time


def disk(path):
    result = subprocess.run(['du', '-s', '-B1', str(path)], capture_output=True, text=True)
    return int(result.stdout.split()[0]) if result.stdout.strip() else 0


def compare(root):
    records = [json.loads(p.read_text()) for p in Path(root).rglob('result.json')]
    lines = ['| Variant | Sample | Outcome | Build seconds | Test seconds | Peak target GiB |',
             '| --- | ---: | --- | ---: | ---: | ---: |']
    for r in sorted(records, key=lambda x: (x['sample'], x['variant'])):
        phases = {p['name']: p for p in r['phases']}
        lines.append(f"| {r['variant']} | {r['sample']} | {r['outcome']} | "
                     f"{phases.get('cargo-build', {}).get('wall_seconds', 'n/a')} | "
                     f"{phases.get('cargo-test', {}).get('wall_seconds', 'n/a')} | "
                     f"{r['peak_target_bytes'] / 2**30:.3f} |")
    valid = len(records) == 6 and all(r['outcome'] == 'success' for r in records)
    valid = valid and all(sorted(r['sample'] for r in records if r['variant'] == v) == [1, 2, 3]
                          for v in ['baseline', 'implementation'])
    keys = ['runner_image', 'runner_image_version', 'rustc', 'cargo', 'go', 'cpu_count', 'cpu_model', 'memory_total_kib', 'kernel']
    valid = valid and all(len({str(r.get(k)) for r in records}) == 1 for k in keys)
    if valid:
        values = {}
        for v in ['baseline', 'implementation']:
            group = [r for r in records if r['variant'] == v]
            values[v] = [next(p['wall_seconds'] for p in r['phases'] if p['name'] == 'cargo-test') for r in group]
            lines.append(f"\n{v} test seconds: {values[v]}; median {statistics.median(values[v]):.3f}; "
                         f"range {min(values[v]):.3f} to {max(values[v]):.3f}.")
        improvement = (1 - statistics.median(values['implementation']) / statistics.median(values['baseline'])) * 100
        lines.append(f'\nMedian Ubuntu cargo test improvement: {improvement:.2f}%. Target >=25%: {improvement >= 25}.')
        peaks = {v: statistics.median(r['peak_target_bytes'] for r in records if r['variant'] == v)
                 for v in ['baseline', 'implementation']}
        lines.append(f"\nMedian peak disk reduction: {(1-peaks['implementation']/peaks['baseline'])*100:.2f}%.")
        lines.append(f"Historical 13.5 GiB disk target: {peaks['implementation'] <= 13.5 * 2**30}.")
    else:
        lines.append('\nTiming acceptance is unverified: six successful comparable samples are required. Inspect result.json and logs.')
    text = '\n'.join(lines) + '\n'
    print(text)
    Path(root, 'comparison.md').write_text(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(text)
    return 0 if valid else 1


def measure(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    target = Path(os.environ['CARGO_TARGET_DIR'])
    if target.exists():
        raise RuntimeError('Benchmark target must not exist before the run')
    result = {'variant': os.environ['BENCH_VARIANT'], 'sample': int(os.environ['BENCH_SAMPLE']),
              'sha': os.environ['BENCH_SHA'], 'runner_image': os.environ.get('ImageOS'),
              'runner_image_version': os.environ.get('ImageVersion'), 'cpu_count': os.cpu_count(),
              'kernel': platform.release(), 'phases': [], 'peak_target_bytes': 0, 'outcome': 'failed'}
    for key, command in [('rustc', ['rustc', '-Vv']), ('cargo', ['cargo', '-V']), ('go', ['go', 'version'])]:
        result[key] = subprocess.check_output(command, text=True).strip()
    result['cpu_model'] = next(line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name'))
    result['memory_total_kib'] = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))
    result['initial_free_bytes'] = shutil.disk_usage(target.parent).free
    Path(os.environ['DRE_TEST_GO_PLUGINS']).mkdir()
    commands = [('go-test', ['go', 'test', './...'], 'go/databricks'),
                ('go-build', ['go', 'build', '-o', str(Path(os.environ['DRE_TEST_GO_PLUGINS']) / 'dre-plugin-databricks'), '.'], 'go/databricks'),
                ('cargo-build', ['cargo', 'build', '--workspace', '--bins'], '.'),
                ('cargo-test', ['cargo', 'test', '--workspace', '--no-fail-fast'], '.')]
    try:
        # Record insufficient capacity instead of deleting runner tools or altering profiles.
        if result['initial_free_bytes'] < 35 * 2**30:
            result['failure'] = 'Less than 35 GiB free; a larger runner is required for a successful baseline.'
            return 1
        for name, command, cwd in commands:
            print(name, command, flush=True)
            start = time.monotonic()
            with (output / (name + '.log')).open('wb') as log:
                process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
                while process.poll() is None:
                    result['peak_target_bytes'] = max(result['peak_target_bytes'], disk(target))
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                wall = time.monotonic() - start
            result['phases'].append({'name': name, 'command': command, 'wall_seconds': round(wall, 3), 'exit_code': process.returncode})
            print(name, result['phases'][-1], flush=True)
            if process.returncode:
                result['failure'] = name + ' exited unsuccessfully; see its log'
                return 1
        result['outcome'] = 'success'
        return 0
    finally:
        result['target_bytes'] = disk(target)
        result['peak_target_bytes'] = max(result['peak_target_bytes'], result['target_bytes'])
        result['deps_bytes'] = disk(target / 'debug/deps')
        result['binaries'] = []
        for name in ['run_xlsx_formats', 'output', 'templates_profiles', 'duckdb_seed']:
            for path in glob.glob(str(target / 'debug/deps' / (name + '-*'))):
                p = Path(path)
                if p.is_file() and os.access(p, os.X_OK) and not p.suffix:
                    symbols = subprocess.run(['nm', '-C', path], capture_output=True, text=True)
                    result['binaries'].append({'path': str(p.relative_to(target)), 'bytes': p.stat().st_size,
                                               'nm_exit_code': symbols.returncode,
                                               'duckdb_symbols': 'libduckdb_sys' in symbols.stdout or 'duckdb_open' in symbols.stdout})
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2), flush=True)
        # This exact disposable target was created by this job only.
        if target.exists():
            shutil.rmtree(target)


if __name__ == '__main__':
    sys.exit(compare(sys.argv[2]) if sys.argv[1] == '--compare' else measure(sys.argv[1]))
