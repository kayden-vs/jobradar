# JobRadar on your Windows laptop (Arch WSL)

This setup uses Windows Task Scheduler and `scripts/laptop_tick.py`. It targets
09:00 and 19:00 India time, checking eligibility every 15 minutes while you are
signed in. The laptop does not have to stay on all day.

## What the schedule means

| Situation | Behavior |
|---|---|
| Laptop available all day | One run around 09:00 and one around 19:00 IST |
| Open laptop at 11:00 | Run the current morning window on login or within about 15 minutes |
| Open laptop at 20:00 after two days off | One current evening run; all old windows discarded |
| Open laptop before 09:00 | Wait until 09:00; no yesterday-evening catch-up |
| Morning catch-up at 18:00 | Skip the evening run that day: minimum six hours between starts |
| No internet before a run | Do not consume the window; check again next tick |
| Internet fails after launch, crash, shutdown, or timeout | Window is consumed; no automatic rerun storm |
| Another guarded pipeline run is active | Skip this check; never queue another process |
| Screen locked | Runs while your Windows account remains signed in and computer stays awake |
| Signed out, sleeping, hibernating, or powered off | Does not run; considers only current window when available again |
| Clock goes backward | Wait until the minimum time gap is satisfied; do not erase state |

Two runs are a target, not a promise when the laptop is unavailable. The helper
permits at most one attempt per window. It deliberately records an attempt
before starting, so an interrupted run does not repeat on every restart.
Before 09:00 no window is eligible; morning is 09:00–18:59, evening 19:00–23:59.
The six-hour gap may delay an evening run or skip it entirely.

## 1. Verify WSL and the local environment

Open **Windows PowerShell as your normal Windows user**, the one who owns Arch
WSL. Do not use a different administrator account or SYSTEM.

```powershell
wsl --list --verbose
wsl -d Arch -u rohit --exec /home/rohit/projects/jobradar/venv/bin/python --version
```

Confirm the distribution is named `Arch` and the Python command succeeds. If
your distribution name differs, replace `Arch` in the remaining commands.

In your **Arch terminal**:

```bash
cd /home/rohit/projects/jobradar
test -f .env && echo '.env exists'
test -f profile.yaml && echo 'profile.yaml exists'
command -v timeout
venv/bin/python main.py profile.yaml --dry-run
venv/bin/python scripts/laptop_tick.py --status
```

The dry run loads configuration and initializes the local database, without
calling sources or sending messages. `--status` does not launch the pipeline,
consume a window, or test API credentials. Keep your existing working venv;
do not reinstall dependencies unnecessarily.

Confirm your local `.env` has the current Gemini, Serper, Telegram bot and
Telethon session credentials. Do not paste credentials into Task Scheduler.
If migrating the latest AWS database, stop both pipelines and make a consistent
SQLite backup first. Do not overwrite your local database casually or copy a
live WAL-mode database without its pending transactions.

**Never schedule `run.sh` on the laptop: that is the EC2 shutdown wrapper.**

## 2. Register one Windows scheduled task

Paste this into **Windows PowerShell**, using the same Windows account as above.
This registers the task but does not manually start a pipeline. The first
automatic eligibility check occurs approximately one minute later and can
start a real run if the current window is due.

```powershell
$ErrorActionPreference = 'Stop'
$taskName = 'JobRadar Laptop'
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute "$env:SystemRoot\System32\wsl.exe" `
    -Argument '-d Arch -u rohit --exec /home/rohit/projects/jobradar/venv/bin/python /home/rohit/projects/jobradar/scripts/laptop_tick.py'

# No repetition duration means repeat indefinitely.
$poll = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)
$login = New-ScheduledTaskTrigger -AtLogOn -User $userId

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 65)
$principal = New-ScheduledTaskPrincipal -UserId $userId `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action `
    -Trigger @($poll, $login) -Settings $settings -Principal $principal `
    -Description 'Check every 15 minutes; only current 09:00/19:00 IST window, no backlog.'
```

If a task with this name already exists, inspect it instead of adding another.
The command intentionally does not overwrite existing tasks. If Windows denies
registration, open PowerShell as administrator **under the same Windows account**
and repeat. No Windows password is stored because this uses your signed-in session.

## 3. Check the task settings

Open **Task Scheduler → Task Scheduler Library → JobRadar Laptop → Properties**:

- **General:** your Windows user; “Run only when user is logged on.”
- **Triggers:** one repeating 15-minute trigger, plus your logon trigger.
- **Conditions:** idle-only disabled; AC-only disabled; stopping on battery disabled;
  “Wake the computer to run this task” disabled.
- **Settings:** missed-start catch-up enabled; “If the task is already running”
  set to **Do not start a new instance**; automatic restart on failure disabled.

Missed Windows checks may trigger an eligibility check after downtime. The Python
guard discards old windows regardless of how many checks Windows missed.
There is no list of missed pipeline runs to replay.

Leave this as the only automatic JobRadar scheduler on the laptop. Disable any
older JobRadar Windows task or WSL cron entry if you previously created one.
Do not disable unrelated scheduled tasks.

## 4. Validate the installation

In PowerShell:

```powershell
Get-ScheduledTask -TaskName 'JobRadar Laptop' | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName 'JobRadar Laptop' |
    Select-Object LastRunTime, LastTaskResult, NextRunTime
wsl -d Arch -u rohit --exec /home/rohit/projects/jobradar/venv/bin/python /home/rohit/projects/jobradar/scripts/laptop_tick.py --status
```

To request an immediate check:

```powershell
Start-ScheduledTask -TaskName 'JobRadar Laptop'
```

**This can run the real pipeline and send Telegram notifications** if due. It
does not bypass the window or six-hour guard. If a window was already attempted,
the task exits normally without another run.

In Arch, inspect:

```bash
cd /home/rohit/projects/jobradar
tail -n 30 data/laptop-scheduler.log
tail -n 50 data/laptop-run.log
venv/bin/python scripts/laptop_tick.py --status
```

`laptop-scheduler.log` records starts, exits, offline checks and errors.
`laptop-run.log` contains the latest run's console output and is replaced on the
next real run. Existing `profile.log` rotation is unchanged. A scheduler exit
code of zero can mean “not due”; it does not prove sources or credentials work.
Inspect the pipeline summary. Exit code 124 normally means the one-hour timeout.

## 5. Everyday use and recovery

- You may close the terminal; Task Scheduler launches WSL itself.
- Keep the laptop awake and connected during a run. This setup does not prevent
  lid-close sleep or wake a sleeping laptop. If you want it to finish while
  unattended, set a sufficiently long sleep timeout in Windows power settings.
- Shutdown/power loss can interrupt a run. Existing pipeline database state
  survives; already-saved pending work is reconsidered by subsequent runs.
  The schedule guard does not retry the interrupted window.
- `data/laptop-schedule.json` is persistent scheduling state. Do not delete it
  to troubleshoot routine failures: that would permit a repeat attempt. If it
  is corrupt, the helper stops rather than assuming nothing ran. Inspect the
  scheduler log and repair/restore state deliberately.
- The helper and portable runner share `data/pipeline.lock`. They prevent
  overlap, but a direct `python main.py` bypasses that protection. For routine
  manual runs, use `Start-ScheduledTask` above so frequency limits also apply.
- Socket connectivity is only an offline check, not proof every API works.
  Captive portals, expired keys, quotas, and provider outages remain visible
  in pipeline logs. Failures after launch consume the current attempt.
- A one-hour GNU timeout, with a 30-second forced-kill grace period, bounds
  normal running time. Sleeping pauses execution; do not interpret this as a
  guarantee of one hour of wall time across sleep or hibernation.
- Follow-up drafts from the EC2 wrapper are not added to this task. The main
  discovery pipeline and its existing weekly-summary logic run normally.
- No scheduler can guarantee exactly-once Telegram delivery if the laptop loses
  power after Telegram accepts a message but before the database acknowledges it.

To pause or resume automatic checks in PowerShell:

```powershell
Disable-ScheduledTask -TaskName 'JobRadar Laptop'
# Later:
Enable-ScheduledTask -TaskName 'JobRadar Laptop'
```

Disabling prevents future starts; it does not stop a pipeline already running.
To remove scheduling, after any active run finishes:

```powershell
Unregister-ScheduledTask -TaskName 'JobRadar Laptop'
```

## References and validation limits

- [Windows task settings](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset)
- [Windows task triggers](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger)
- [WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)

The Python scheduling guard is covered by local regression tests. Task registration,
Windows resume behavior, and a real scheduled pipeline run must be verified on your
Windows session; they have not been executed by the assistant.
