"""Qualify native env precedence and shared projection with the installed CLI."""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.integration.omnigent import test_claude_settings_environment as journey_tests
from tests.integration.omnigent.test_claude_deny_cli import _run_probe

session_factory = journey_tests.session_factory
native_journey = journey_tests.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_PROBE = r"""
import json, os, pathlib, shutil, socket, subprocess, sys, time, uuid
import httpx
root, cli, temporary, mode, base, projected, selected, layout = json.loads(sys.argv[1])
p=pathlib.Path(temporary);p.mkdir(parents=True)
os.environ['HOME']=str(p/'runner');pathlib.Path(os.environ['HOME']).mkdir()
from omnigent.harnesses.claude_native.bridge import augment_claude_args, bridge_dir_for_bridge_id, prepare_bridge_dir
home=p/'home';home.mkdir();config=home/pathlib.Path(layout['credentialHome']).name;config.mkdir();workspace=p/'workspace';workspace.mkdir()
if layout.get('processHomeConfiguration'):
 from moonmind.provider_profiles.claude_configuration import materialize_process_home
 home=pathlib.Path(materialize_process_home(layout['processHomeConfiguration'],runtime_support_dir=p/'support',workspace_path=str(workspace)))
 # A competing default directory must not supply selected preferences.
 (home/'.claude').mkdir();(home/'.claude'/'settings.json').write_text(json.dumps({'env':{'PROJECT_LABEL':'wrong-home'}}))
(config/'settings.json').write_text(json.dumps(selected))
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
 args=base
 if mode!='native':
  bridge_id='env-test-'+uuid.uuid4().hex;bridge=bridge_dir_for_bridge_id(bridge_id);prepare_bridge_dir(bridge_id,workspace=workspace)
  args=augment_claude_args(base,bridge_dir=bridge,python_executable=sys.executable,skills_filter=mode)
  generated=json.loads(pathlib.Path(args[args.index('--settings')+1]).read_text());assert generated.get('hooks')
 body={'command':'printf "label=%s only=%s sibling=%s" "$PROJECT_LABEL" "$PROJECT_ONLY" "$PROJECT_SIBLING"','description':'Inspect nonsecret project values'}
 if layout.get('processHomeConfiguration'):
  body['command'] += '; cat "$HOME/NOTES.md"'
 client.post(url+'/mock/configure',json={'responses':[{'tool_calls':[{'name':'Bash','arguments':json.dumps(body)}]},{'text':'done'}]}).raise_for_status()
 env={'PATH':os.defpath,'HOME':str(home),'CLAUDE_CONFIG_DIR':str(config),'ANTHROPIC_BASE_URL':url,'ANTHROPIC_API_KEY':'mock-key','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS':'1','HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':'127.0.0.1,localhost','PYTHONPATH':os.environ.get('PYTHONPATH','')}
 env.update({'PROJECT_LABEL':'process-value','PROJECT_SIBLING':'process-sibling'} if mode=='native' else projected)
 run=subprocess.run([cli,'-p','Complete the local test','--output-format','stream-json','--verbose',*args],cwd=workspace,env=env,text=True,capture_output=True,timeout=45,check=False)
 assert run.returncode==0,run.stderr[-1000:]
 def nodes(value):
  if isinstance(value,dict):
   yield value
   for item in value.values():yield from nodes(item)
  elif isinstance(value,list):
   for item in value:yield from nodes(item)
 entries=list(nodes(client.get(url+'/mock/message_requests').json()))
 uses={item['id'] for item in entries if item.get('type')=='tool_use' and item.get('name')=='Bash' and item.get('input',{}).get('command')==body['command']}
 results=[item for item in entries if item.get('type')=='tool_result' and item.get('tool_use_id') in uses]
 assert uses and results,'Bash was not actually attempted'
 for result in results:
  assert not result.get('is_error'),result
  assert 'label=settings-value only=settings-only sibling=process-sibling' in json.dumps(result),result
  if layout.get('processHomeConfiguration'):
   assert 'Admitted HOME for '+str(workspace)+'.' in json.dumps(result),result
 print(json.dumps({'version':subprocess.check_output([cli,'--version'],text=True,env=env).strip(),'mode':mode,'actualToolOutputVerified':True}))
finally:
 server.terminate();server.wait(timeout=5)
 if bridge:shutil.rmtree(bridge)
"""


@pytest.mark.parametrize("native_journey", [
    *journey_tests.SELECTIONS,
    {
        "harness": "claude-native", "claudeCredentialMount": "/home/app/selected-claude-auth",
        "claudeOauthFiles": True, "claudePreferences": True, "processHome": True,
        "sessionSettings": {"env": journey_tests.ENV},
        "oauthTuning": {"PROJECT_LABEL": "process-value", "PROJECT_SIBLING": "process-sibling"},
    },
], indirect=True)
@pytest.mark.parametrize("mode", ["native", "all", "none"])
async def test_installed_cli_preserves_native_settings_env(
    native_journey, tmp_path, mode
):
    cli = shutil.which("claude")
    if cli is None:
        pytest.skip("installed Claude CLI unavailable; image qualification is separate")
    root = Path(__file__).resolve().parents[3] / "omnigent"
    env = {
        **os.environ,
        "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    available = await asyncio.to_thread(
        _run_probe,
        [
            sys.executable,
            "-c",
            "from omnigent.harnesses.claude_native.bridge import augment_claude_args",
        ],
        env,
    )
    if available.returncode and "ModuleNotFoundError:" in available.stderr:
        pytest.skip("installed Omnigent runtime dependencies unavailable")
    assert available.returncode == 0, available.stderr[-1000:]
    journey = native_journey
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    journey_tests.assert_environment(journey)
    projected = {key: journey.docker.launch_env[key] for key in journey_tests.EXPECTED}
    if journey.docker.claude_preferences is not None:
        selected = json.loads(journey.docker.claude_preferences["settings.json"])
    else:
        selected = next(
            item["content_template"]
            for item in journey.docker.claude_configuration["fileTemplates"]
            if item["path"].endswith("/settings.json")
        )
    from moonmind.omnigent.runtime_bindings import stable_binding_id
    from moonmind.provider_profiles.claude_configuration import claude_process_home_from_handles

    binding = await journey.services.runtime_binding_store.get(stable_binding_id(
        execution_plan_ref=journey.plan.planRef, idempotency_key=journey.request.idempotency_key,
    ))
    process_home, configuration = claude_process_home_from_handles(list(binding.credentialRuntimeHandles.values()))
    assert bool(process_home) == bool(journey.selection.get("processHome"))
    if process_home:
        assert journey.docker.launch_env["CLAUDE_CONFIG_DIR"] == "/home/app/selected-claude-auth"
        assert journey.server.create_payloads[-1]["process_home"] == process_home
    layout = {"credentialHome": journey.docker.launch_env.get("CLAUDE_CONFIG_DIR", "/home/app/.claude"),
              "processHomeConfiguration": configuration}
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
                    mode,
                    journey.server.create_payloads[-1]["terminal_launch_args"],
                    projected,
                    selected,
                    layout,
                ]
            ),
        ],
        env,
    )
    assert probe.returncode == 0, probe.stderr[-2000:]
    assert json.loads(probe.stdout.strip().splitlines()[-1])["actualToolOutputVerified"]
