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
root, binary, temporary, configuration, node_directory = json.loads(sys.argv[1])
r=pathlib.Path(temporary);r.mkdir();home=r/'home';home.mkdir();source=r/'selected-codex-auth';source.mkdir();private=r/'private';private.mkdir();sibling=r/'sibling';sibling.mkdir()
os.environ['HOME']=str(home);os.environ['CODEX_HOME']=str(source)
from omnigent.inner.codex_executor import _codex_home_config_source_from_env, _populate_codex_home_config
from moonmind.provider_profiles import codex_configuration as config
assert _codex_home_config_source_from_env()==source
(source/'config.toml').write_text('model = "selected-layout-sentinel"\nmodel_verbosity = "high"\n')
(source/'AGENTS.md').write_text('Source instructions remain unchanged.')
(sibling/'config.toml').write_text('model = "wrong-account"\n')
(sibling/'auth.json').write_text('synthetic sibling auth unchanged')
encode=lambda value:base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
token=encode({'alg':'none'})+'.'+encode({'email':'synthetic@example.invalid','https://api.openai.com/auth':{'chatgpt_account_id':'synthetic-account','chatgpt_plan_type':'plus'}})+'.synthetic'
(source/'auth.json').write_text(json.dumps({'auth_mode':'chatgpt','OPENAI_API_KEY':None,'tokens':{'id_token':token,'access_token':'synthetic-access','refresh_token':'synthetic-refresh','account_id':'synthetic-account'},'last_refresh':'2026-11-19T00:00:00Z'}))
# Exclude only observed native CLI-owned runtime outputs, never arbitrary new files.
runtime_files={'installation_id',*(name+suffix for name in ('queue_1.sqlite','logs_2.sqlite','memories_1.sqlite','state_5.sqlite','goals_1.sqlite') for suffix in ('','-wal','-shm'))}
def snapshot(directory):
 result={}
 for p in directory.rglob('*'):
  relative=p.relative_to(directory);name=relative.as_posix()
  if name in runtime_files or name.startswith(('tmp/arg0-macos/','tmp/arg0/','skills/.system/')):continue
  if p.is_file():result[name]=p.read_bytes()
 return result
(source/'rules').mkdir();(source/'rules/preferences.json').write_text('{"seed":"mutable enrollment"}')
before=snapshot(source)
env={'PATH':os.defpath+(os.pathsep+node_directory if node_directory else ''),'HOME':str(home),'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1'}
for directory,expected in [(home,False),(source,True)]:
 result=subprocess.run([binary,'login','status'],env={**env,'CODEX_HOME':str(directory)},text=True,capture_output=True,timeout=10)
 assert ('Logged in using ChatGPT' in result.stdout+result.stderr) is expected, result.stderr
_populate_codex_home_config(private,source,minimal_config=False)
assert (private/'auth.json').is_symlink() and (private/'auth.json').resolve()==source/'auth.json'
# Execute the production generated wrapper, not merely the overlay helpers.
configuration_path=r/'configuration.json';configuration_path.write_text(json.dumps(configuration))
# Only remap the admitted container coordinate into this private Mac fixture.
# Keep the production parser and all file/auth protections unchanged.
helper=r/'codex_configuration.py'
helper.write_text(pathlib.Path(config.__file__).read_text().replace('_ENROLLED_CODEX_HOME = "/home/app/.codex"','_ENROLLED_CODEX_HOME = '+repr(str(source)),1))
import shutil
shutil.copyfile(pathlib.Path(config.__file__).with_name('template_renderer.py'),r/'template_renderer.py')
wrapper=r/'codex-wrapper'
context=r/'context';context.write_text('#!/bin/sh\nexec "$@"\n');context.chmod(0o700)
subprocess.run([sys.executable,str(helper),'prepare',str(configuration_path) if configuration else '-',binary,str(wrapper),str(context),str(source)],env={**env,'CODEX_HOME':str(private)},check=True,capture_output=True,timeout=10)
assert snapshot(source)==before
assert (sibling/'config.toml').read_text()=='model = "wrong-account"\n'
async def read(directory,wrapped=False):
 p=await asyncio.create_subprocess_exec(str(wrapper) if wrapped else binary,'app-server',env={**env,'CODEX_HOME':str(directory)},stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
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
 native=await read(source);shared=await read(private,True)
 assert native['model']==shared['model']=='selected-layout-sentinel'
 assert native['model_verbosity']=='high'
 assert shared['model_verbosity']==('low' if configuration else 'high')
 if configuration and configuration.get('additionalFiles'):
  seeded=configuration.get('fileSeeds')
  expected=('Captured {{workspace_path}} instructions\nExactly once addition' if seeded else 'Frozen instructions\nExactly once addition')
  expected_json=({'seed':'captured {{workspace_path}}','extra':2} if seeded else {'frozen':True,'extra':2})
  def assert_private(directory):
   assert (directory/'AGENTS.md').read_text()==expected
   assert json.loads((directory/'rules/preferences.json').read_text())==expected_json
   assert (directory/'auth.json').is_symlink() and (directory/'auth.json').resolve()==source/'auth.json'
  # Native runtime uses this same ordered materializer, independently of the wrapper.
  from types import SimpleNamespace
  from moonmind.workflows.adapters.materializer import ProviderProfileMaterializer
  native_home=r/'native-files';native_home.mkdir()
  (native_home/'AGENTS.md').write_text('Native enrollment seed')
  if seeded:
   for seed in seeded['files']:
    target=native_home/seed['path'];target.parent.mkdir(parents=True,exist_ok=True)
    if seed['present']:target.write_text(seed['content'])
  renderer=ProviderProfileMaterializer(base_env={},secret_resolver=None)
  for template in configuration['additionalFiles']:
   renderer._materialize_file_template(SimpleNamespace(**template),{'runtime_support_dir':str(native_home),'workspace_path':str(pathlib.Path.cwd())})
  assert (native_home/'AGENTS.md').read_text()==expected
  assert (native_home/'AGENTS.md').read_bytes()==(private/'AGENTS.md').read_bytes()
  assert (native_home/'rules/preferences.json').read_bytes()==(private/'rules/preferences.json').read_bytes()
  assert_private(private)
  # Actual wrapper retry must not append twice.
  await read(private,True);assert_private(private)
  assert snapshot(source)==before
  # A cold private home sees changed enrollment seed, but never admits those bytes.
  (source/'AGENTS.md').write_text('Changed unadmitted enrollment seed')
  (source/'rules/preferences.json').write_text('{"different":true}')
  changed=snapshot(source)
  cold=r/'cold';cold.mkdir();_populate_codex_home_config(cold,source,minimal_config=False)
  await read(cold,True);assert_private(cold)
  assert snapshot(source)==changed
  assert (source/'auth.json').read_bytes()==before['auth.json']
 assert (sibling/'config.toml').read_text()=='model = "wrong-account"\n'
 assert (sibling/'auth.json').read_text()=='synthetic sibling auth unchanged'
asyncio.run(main())
print(json.dumps({'selectedAuth':True,'privateConfigRead':True,'sourceInputsUnchanged':True}))
'''


@pytest.mark.parametrize(
    "native_journey,frozen_seed",
    [(selection, False) for selection in journey_tests.SELECTIONS]
    + [(journey_tests.SELECTIONS[-1], True)],
    indirect=["native_journey"],
)
async def test_installed_codex_preserves_selected_auth_and_private_configuration(native_journey, tmp_path, frozen_seed):
    cli = shutil.which("codex")
    if cli is None:
        pytest.skip("installed Codex CLI unavailable; image qualification is separate")
    journey = native_journey
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    journey_tests.assert_selected_configuration(journey)
    assert journey.docker.launch_env["CODEX_HOME"] == journey_tests.MOUNT
    configuration = json.loads(journey.docker.launch_env.get("MOONMIND_CODEX_PROFILE_CONFIGURATION", "null"))
    if frozen_seed:
        assert configuration and configuration.get("additionalFiles")
        configuration["additionalFiles"] = [item for item in configuration["additionalFiles"] if item["merge_strategy"] != "replace"]
        configuration["fileSeeds"] = {"version": 1, "files": [
            {"path": "AGENTS.md", "present": True, "content": "Captured {{workspace_path}} instructions"},
            {"path": "rules/preferences.json", "present": True, "content": '{"seed":"captured {{workspace_path}}"}'},
        ]}
    root = Path(__file__).resolve().parents[3] / "omnigent"
    environment = {**os.environ, "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    probe = await asyncio.to_thread(_run_probe, [sys.executable, "-c", _PROBE,
        json.dumps([str(root), cli, str(tmp_path / "cli"), configuration, str(Path(shutil.which("node")).parent) if shutil.which("node") else None])], environment)
    assert probe.returncode == 0, probe.stderr[-2000:]
    assert json.loads(probe.stdout.strip().splitlines()[-1])["sourceInputsUnchanged"]
