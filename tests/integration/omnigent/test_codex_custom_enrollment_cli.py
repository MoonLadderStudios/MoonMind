"""Offline vendor source selection and upstream private-home composition."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from tests.integration.omnigent import test_codex_custom_credential_mount as journey_tests
from tests.integration.omnigent.test_claude_deny_cli import _run_probe

session_factory = journey_tests.session_factory
native_journey = journey_tests.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_PROBE = r'''
import asyncio, base64, json, os, pathlib, subprocess, sys
root, binary, temporary, configuration = json.loads(sys.argv[1])
r=pathlib.Path(temporary);r.mkdir();home=r/'home';home.mkdir();source=r/'selected-codex-auth';source.mkdir();private=r/'private';private.mkdir();sibling=r/'sibling';sibling.mkdir()
os.environ['HOME']=str(home);os.environ['CODEX_HOME']=str(source)
from omnigent.inner.codex_executor import _codex_home_config_source_from_env, _populate_codex_home_config
from moonmind.provider_profiles import codex_configuration as config
assert _codex_home_config_source_from_env()==source
(source/'config.toml').write_text('model = "selected-layout-sentinel"\nmodel_verbosity = "high"\n')
(source/'AGENTS.md').write_text('Source instructions remain unchanged.')
(sibling/'config.toml').write_text('model = "wrong-account"\n')
encode=lambda value:base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
token=encode({'alg':'none'})+'.'+encode({'email':'synthetic@example.invalid','https://api.openai.com/auth':{'chatgpt_account_id':'synthetic-account','chatgpt_plan_type':'plus'}})+'.synthetic'
(source/'auth.json').write_text(json.dumps({'auth_mode':'chatgpt','OPENAI_API_KEY':None,'tokens':{'id_token':token,'access_token':'synthetic-access','refresh_token':'synthetic-refresh','account_id':'synthetic-account'},'last_refresh':'2026-11-19T00:00:00Z'}))
before={p.name:p.read_bytes() for p in source.iterdir()}
env={'PATH':os.defpath,'HOME':str(home),'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1'}
for directory,expected in [(home,False),(source,True)]:
 result=subprocess.run([binary,'login','status'],env={**env,'CODEX_HOME':str(directory)},text=True,capture_output=True,timeout=10)
 assert ('Logged in using ChatGPT' in result.stdout+result.stderr) is expected
_populate_codex_home_config(private,source,minimal_config=False)
assert (private/'auth.json').is_symlink() and (private/'auth.json').resolve()==source/'auth.json'
# Translate the exact selected container mount into this isolated local fixture.
# Production passes it from the immutable credential handle in the wrapper command.
config._ENROLLED_CODEX_HOME=str(source)
if configuration:
 config.configure_private_oauth_home(configuration,private,enrolled_home=str(source))
assert {p.name:p.read_bytes() for p in source.iterdir()}==before
assert (sibling/'config.toml').read_text()=='model = "wrong-account"\n'
async def read(directory):
 p=await asyncio.create_subprocess_exec(binary,'app-server',env={**env,'CODEX_HOME':str(directory)},stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
 async def rpc(id,method,params):
  p.stdin.write((json.dumps({'id':id,'method':method,'params':params})+'\n').encode());await p.stdin.drain()
  while True:
   line=await asyncio.wait_for(p.stdout.readline(),15)
   assert line,'app-server stopped before read'
   response=json.loads(line)
   if response.get('id')==id:return response
 try:
  await rpc(1,'initialize',{'clientInfo':{'name':'selected-layout-test','version':'1'}})
  result=await rpc(2,'config/read',{'includeLayers':False})
  assert 'error' not in result,result.get('error')
  return result['result']['config']
 finally:
  p.terminate();await asyncio.wait_for(p.wait(),5)
async def main():
 native=await read(source);shared=await read(private)
 assert native['model']==shared['model']=='selected-layout-sentinel'
 assert native['model_verbosity']=='high'
 assert shared['model_verbosity']==('low' if configuration else 'high')
asyncio.run(main())
print(json.dumps({'selectedAuth':True,'privateConfigRead':True,'sourceUnchanged':True}))
'''


@pytest.mark.parametrize("native_journey", journey_tests.SELECTIONS, indirect=True)
async def test_installed_codex_preserves_selected_auth_and_private_configuration(native_journey, tmp_path):
    cli = shutil.which("codex")
    if cli is None:
        pytest.skip("installed Codex CLI unavailable; image qualification is separate")
    journey = native_journey
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    journey_tests.assert_selected_configuration(journey)
    assert journey.docker.launch_env["CODEX_HOME"] == journey_tests.MOUNT
    configuration = json.loads(journey.docker.launch_env.get("MOONMIND_CODEX_PROFILE_CONFIGURATION", "null"))
    root = Path(__file__).resolve().parents[3] / "omnigent"
    environment = {**os.environ, "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    probe = await asyncio.to_thread(_run_probe, [sys.executable, "-c", _PROBE,
        json.dumps([str(root), cli, str(tmp_path / "cli"), configuration])], environment)
    assert probe.returncode == 0, probe.stderr[-2000:]
    assert json.loads(probe.stdout.strip().splitlines()[-1])["sourceUnchanged"]
