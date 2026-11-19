"""Optional installed-Claude qualification against the existing local mock API.

No external provider is contacted. These tests require the installed Claude CLI
and Omnigent's Python runtime dependencies; they do not qualify a Docker image.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.omnigent import test_generic_native_durable_journey as native

session_factory = native.session_factory
native_journey = native.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_PROBE = r"""
import json, os, pathlib, shutil, socket, subprocess, sys, time, uuid
import httpx
root, cli, temporary, mode, base = json.loads(sys.argv[1])
os.environ["HOME"]=str(pathlib.Path(temporary)/"runner")
pathlib.Path(os.environ["HOME"]).mkdir(parents=True)
from omnigent.harnesses.claude_native.bridge import augment_claude_args, bridge_dir_for_bridge_id, prepare_bridge_dir
p=pathlib.Path(temporary); home=p/'home';home.mkdir();config=home/'.claude';config.mkdir();workspace=p/'workspace';workspace.mkdir()
forbidden=workspace/'forbidden file.txt';forbidden.write_text('DENIED_READ_SENTINEL')
allowed=workspace/'allowed file.txt';allowed.write_text('ALLOWED_READ_SENTINEL')
written=workspace/'forbidden-write'
forbidden_edit=workspace/'forbidden-edit';forbidden_edit.mkdir()
allowed_edit=workspace/'allowed-edit';allowed_edit.mkdir()
for directory in (forbidden_edit,allowed_edit):(directory/'sample.txt').write_text('original text')
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();url=f'http://127.0.0.1:{port}'
client=httpx.Client(trust_env=False)
server=subprocess.Popen([sys.executable,str(pathlib.Path(root)/'tests/server/integration/mock_llm_server.py'),str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
bridge=None
try:
 for _ in range(100):
  try:
   if client.get(url+'/stats').status_code==200:break
  except httpx.ConnectError:pass
  time.sleep(.1)
 else:raise RuntimeError('local mock API did not start')
 bridge_id='deny-test-'+uuid.uuid4().hex;bridge=bridge_dir_for_bridge_id(bridge_id);prepare_bridge_dir(bridge_id,workspace=workspace)
 args=augment_claude_args(base,bridge_dir=bridge,python_executable=sys.executable,skills_filter=mode)
 generated=json.loads(pathlib.Path(args[args.index('--settings')+1]).read_text())
 assert generated.get('hooks')
 calls=[('Read',{'file_path':str(forbidden)}),('Read',{'file_path':str(allowed)}),('Bash',{'command':'touch '+str(written),'description':'Forbidden command'}),('Bash',{'command':'printf ALLOWED_COMMAND_SENTINEL','description':'Allowed command'})]
 calls += [('Read',{'file_path':str(directory/'sample.txt')}) for directory in (forbidden_edit,allowed_edit)]
 calls += [('Write',{'file_path':str(directory/'new.txt'),'content':'new content'}) for directory in (forbidden_edit,allowed_edit)]
 calls += [('Edit',{'file_path':str(directory/'sample.txt'),'old_string':'original text','new_string':'changed text'}) for directory in (forbidden_edit,allowed_edit)]
 responses=[{'tool_calls':[{'name':name,'arguments':json.dumps(body)}]} for name,body in calls]+[{'text':'done'}]
 client.post(url+'/mock/configure',json={'responses':responses}).raise_for_status()
 env={'PATH':os.defpath,'HOME':str(home),'CLAUDE_CONFIG_DIR':str(config),'ANTHROPIC_BASE_URL':url,'ANTHROPIC_API_KEY':'mock-key','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS':'1','HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':'127.0.0.1,localhost','PYTHONPATH':os.environ.get('PYTHONPATH','')}
 run=subprocess.run([cli,'-p','Complete the local test','--output-format','stream-json','--verbose',*args],cwd=workspace,env=env,text=True,capture_output=True,timeout=45,check=False)
 assert run.returncode==0,run.stderr[-1000:]
 recorded=client.get(url+'/mock/message_requests').json()
 def nodes(value):
  if isinstance(value,dict):
   yield value
   for item in value.values():yield from nodes(item)
  elif isinstance(value,list):
   for item in value:yield from nodes(item)
 entries=list(nodes(recorded))
 results=[item for item in entries if item.get('type')=='tool_result']
 for index,(name,body) in enumerate(calls):
  uses={item['id'] for item in entries if item.get('type')=='tool_use' and item.get('name')==name and all(item.get('input',{}).get(key)==value for key,value in body.items())}
  matched=[item for item in results if item.get('tool_use_id') in uses]
  assert uses and matched,('tool call was not actually attempted',name,index)
  for result in matched:
   content=json.dumps(result.get('content',''))
   if index in (0,2,6,8):assert result.get('is_error') is True and 'denied' in content.lower(),('expected actual permission refusal',result)
   else:
    assert not result.get('is_error'),('allowed sibling was denied',result)
    if index in (1,3):assert ('ALLOWED_READ_SENTINEL' if index==1 else 'ALLOWED_COMMAND_SENTINEL') in content,('missing successful allowed output',result)
 assert not (forbidden_edit/'new.txt').exists()
 assert (forbidden_edit/'sample.txt').read_text()=='original text'
 assert (allowed_edit/'new.txt').read_text()=='new content'
 assert (allowed_edit/'sample.txt').read_text()=='changed text'
 messages=json.dumps(results)
 print(json.dumps({'version':subprocess.check_output([cli,'--version'],text=True,env=env).strip(),'deniedReadLeaked':'DENIED_READ_SENTINEL' in messages,'allowedReadObserved':True,'deniedCommandRan':written.exists(),'allowedCommandObserved':True,'editRuleEffectsVerified':True,'toolResultObserved':True,'bridgeSettingsPreserved':bool(generated.get('hooks'))}))
finally:
 server.terminate();server.wait(timeout=5)
 if bridge:shutil.rmtree(bridge)
"""


def _run_probe(command, env):
    # One private process group contains the helper, CLI hooks and mock server.
    # Timeout and successful teardown both stop any surviving grandchildren.
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=65)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            # The process group can exit before cleanup reaches it.
            pass
        process.wait()


@pytest.mark.parametrize(
    "native_journey",
    [
        {
            "harness": "claude-native",
            "claudePreferences": True,
            "sessionSettings": {
                "permissions": {
                    "deny": ["Read(**/forbidden file.txt)", "Bash(touch *)", "Edit(**/forbidden-edit/**)"]
                }
            },
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize("skills_filter", ["all", "none"])
async def test_installed_cli_enforces_generated_denies_without_blocking_allowed_siblings(
    native_journey, tmp_path, skills_filter
):
    cli = shutil.which("claude")
    if cli is None:
        pytest.skip(
            "installed Claude CLI unavailable; Docker-image qualification is separate"
        )
    root = Path(__file__).resolve().parents[3] / "omnigent"
    env = {
        **os.environ,
        "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    available = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            "from omnigent.harnesses.claude_native.bridge import augment_claude_args",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if available.returncode and "ModuleNotFoundError:" in available.stderr:
        pytest.skip("installed Omnigent Python runtime dependencies unavailable")
    assert available.returncode == 0, available.stderr[-1000:]
    journey = native_journey
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    root = Path(__file__).resolve().parents[3] / "omnigent"
    env = {
        **os.environ,
        "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    probe = await asyncio.to_thread(
        _run_probe,
        [
            sys.executable,
            "-c",
            _PROBE,
            json.dumps(
                [
                    str(root),
                    cli,
                    str(tmp_path / "cli"),
                    skills_filter,
                    journey.server.create_payloads[-1]["terminal_launch_args"],
                ]
            ),
        ],
        env=env,
    )
    assert probe.returncode == 0, probe.stderr[-2000:]
    evidence = json.loads(probe.stdout.strip().splitlines()[-1])
    assert evidence["version"]
    assert evidence["bridgeSettingsPreserved"] and evidence["toolResultObserved"]
    assert not evidence["deniedReadLeaked"] and not evidence["deniedCommandRan"]
    assert evidence["allowedReadObserved"] and evidence["allowedCommandObserved"]
    assert evidence["editRuleEffectsVerified"]
