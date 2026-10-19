"""Rendered isolation/config contracts, including additive NetworkPolicy rules."""
from pathlib import Path
import shutil
import subprocess
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[3]
pytestmark=pytest.mark.skipif(not shutil.which('helm'),reason='Helm required')


def rendered(enabled=False):
    command=['helm','template','ap',str(ROOT/'charts/agent-platform'),
        '--set','env.AP_SESSION_SECRET=test','--set','env.AP_INTERNAL_SECRET=test']
    if enabled:
        command+=['--set','memoryEmbedding.enabled=true','--set','memoryEmbedding.provision=true',
                  '--set','memoryEmbedding.indexer=true','--set','env.AP_STARTUP_MEMORY_PILOT_AGENTS=pilot',
                  '--set','env.AP_MEMORY_SEMANTIC_AGENTS=pilot','--set','env.AP_MEMORY_SEMANTIC_ENABLED=true']
    return [d for d in yaml.safe_load_all(subprocess.check_output(command,text=True)) if d]


def test_memory_workloads_disabled_by_default():
    assert not any(d['metadata']['name'].startswith(('ap-memory-embedding','ap-memory-model','ap-memory-indexer'))
                   for d in rendered())


def test_rendered_memory_serving_is_local_and_configuration_reaches_backends():
    documents=rendered(True)
    service=next(d for d in documents if d['kind']=='Service' and d['metadata']['name']=='ap-memory-embedding')
    assert service['spec']['type']=='ClusterIP'
    deployment=next(d for d in documents if d['kind']=='Deployment' and d['metadata']['name']=='ap-memory-embedding')
    pod=deployment['spec']['template']['spec']
    assert deployment['spec']['strategy']['type']=='Recreate'
    assert pod['automountServiceAccountToken'] is False
    assert not any('secret' in v for v in pod['volumes'])
    assert pod['containers'][0]['volumeMounts'][0]['readOnly']
    assert '@sha256:' in pod['containers'][0]['image']
    # Policies add together: no namespace-wide allow may reopen Ollama egress.
    labels=deployment['spec']['template']['metadata']['labels']
    for document in documents:
        if document['kind']!='NetworkPolicy':continue
        spec=document['spec'];selector=spec['podSelector']
        selected=all(labels.get(k)==v for k,v in selector.get('matchLabels',{}).items())
        for expression in selector.get('matchExpressions',[]):
            value=labels.get(expression['key'])
            if expression['operator']=='NotIn':selected &= value not in expression['values']
            elif expression['operator']=='In':selected &= value in expression['values']
        if selected and 'Egress' in spec.get('policyTypes',[]):
            assert not spec.get('egress'),document['metadata']['name']
    for name in ('ap-api','ap-dispatcher','ap-recorder'):
        deployment=next(d for d in documents if d['kind']=='Deployment' and d['metadata']['name']==name)
        env={v['name']:v.get('value') for v in deployment['spec']['template']['spec']['containers'][0]['env']}
        assert env['AP_STARTUP_MEMORY_PILOT_AGENTS']=='pilot'
        assert env['AP_MEMORY_SEMANTIC_ENABLED']=='true'
