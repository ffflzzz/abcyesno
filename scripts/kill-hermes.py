import subprocess, sys, os, signal, time

def kill_hermes():
    # Use wmic to list processes and command lines
    try:
        out = subprocess.check_output('wmic process get ProcessId,CommandLine /format:csv', shell=True, text=True, errors='ignore')
    except Exception as e:
        print('wmic failed', e)
        return
    pids = []
    for line in out.splitlines():
        if 'hermes_cli.main' in line and 'serve' in line:
            parts = [p.strip() for p in line.split(',') if p.strip()]
            if parts:
                try:
                    pid = int(parts[-1])
                    pids.append(pid)
                except ValueError:
                    pass
    print('found pids', pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as e:
            print('term failed', pid, e)
    time.sleep(1)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception as e:
            print('kill failed', pid, e)

if __name__ == '__main__':
    kill_hermes()
