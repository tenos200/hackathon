from pathlib import Path
import sys,json,hashlib,copy
from openapi_spec_validator import validate_spec
from jsonschema import Draft202012Validator
R=Path(__file__).resolve().parent.parent
C=R/'contracts';F=C/'fixtures'
api=json.loads((C/'openapi.json').read_text());validate_spec(api)
manifest=json.loads((F/'manifest.json').read_text()); responses={}
for item in manifest['fixtures']:
 response=json.loads((F/item['file']).read_text());responses[item['file']]=response
 schema={'$ref':'#/components/schemas/'+item['schema'],'components':api['components']}
 Draft202012Validator(schema).validate(response)
 # The transport example is also valid for its endpoint/status.
 route=item['route'].split('?')[0]
 path=route
 if path.startswith('/v1/contexts/'):
  path='/v1/contexts/{id}'+('/connections' if path.endswith('/connections') else '/actions' if path.endswith('/actions') else '')
 elif path.startswith('/v1/assertions/'):path='/v1/assertions/{id}'
 elif path.startswith('/v1/calculations/'):path='/v1/calculations/{id}'
 endpoint_schema=api['paths'][path]['get']['responses'][str(item['status'])]['content']['application/json']['schema']
 Draft202012Validator({**endpoint_schema,'components':api['components']}).validate(response)
assert json.loads((C/'fixtures.json').read_text()) == {'manifest':manifest,'responses':responses}
# Defaults form a coherent transport graph. Branches are isolated view tests.
defaults=[responses[i['file']] for i in manifest['fixtures'] if i['scenario']=='default']
assertions={r['data']['id']:r['data'] for r in defaults if 'data' in r and 'predicate' in r['data']}
calculations={r['data']['id']:r['data'] for r in defaults if 'data' in r and 'algorithm_version' in r['data'] and 'result' in r['data']}
contexts={r['data']['id']:r['data'] for r in defaults if 'data' in r and 'disease_profile' in r['data']}
opps={o['id'] for r in defaults for o in r.get('data',{}).get('opportunities',[])}
comparisons={c['id'] for r in defaults for c in r.get('data',{}).get('comparisons',[])}
evidence={e['id'] for a in assertions.values() for e in a['evidence']}
refs=0;quotes=0

def walk(v):
 global refs,quotes
 if isinstance(v,list):
  for x in v:walk(x)
 if not isinstance(v,dict):return
 for k,targets in [('assertion_ids',assertions),('definition_assertion_ids',assertions),('basis_assertion_ids',assertions),('calculation_ids',calculations),('opportunity_ids',opps),('comparison_ids',comparisons),('input_evidence_ids',evidence)]:
  if k in v:
   for id in v[k]:assert id in targets,(k,id);refs+=1
 if 'input_assertion_ids' in v:
  vals=v['input_assertion_ids']
  if isinstance(vals,dict):vals=vals['a']+vals['b']
  for id in vals:assert id in assertions,('input_assertion_ids',id);refs+=1
 if v.get('calculation_id') is not None and 'calculation_id' in v:
  assert v['calculation_id'] in calculations;refs+=1
 if 'text' in v and all(x in v for x in ['assertion_ids','calculation_ids','opportunity_ids']):
  assert v['assertion_ids'] or v['calculation_ids'] or v['opportunity_ids'],v
 if 'context' in v and 'source' in v and v['context']:
  t=v['context'];assert len(t['text'])==t['canonical_end']-t['canonical_start']
  for h in t['highlights']:
   assert t['text'][h['start']:h['end']]==h['quote'];quotes+=1
  # These synthetic views deliberately contain the entire source.
  assert hashlib.sha256(t['text'].encode()).hexdigest()==v['source']['canonical_sha256']
 if 'score' in v and 'reference_coverage' in v:
  if any(x['unassessed_term_ids'] for x in v['reference_coverage'].values()):assert v['score'] is None
  if v['score'] is not None:
   assert abs(sum(x['score_contribution'] or 0 for x in v['shared_terms'])-v['score'])<1e-9
  for side in ['a','b']:
   c=v['reference_coverage'][side];assert c['assessed_terms']+len(c['unassessed_term_ids'])==c['direct_terms']
   if c['direct_terms']:assert c['fraction']==c['assessed_terms']/c['direct_terms']
 for x in v.values():walk(x)
for r in responses.values():walk(r)
assert len([p for p in api['paths'] if p.startswith('/v1/')])==7
assert len(api['paths'])==9
# Negative checks ensure the schema actually rejects obvious incompatible payloads.
original=responses['connections_alpha-loss.json']; validator=Draft202012Validator({'$ref':'#/components/schemas/ConnectionsResponse','components':api['components']})
neg=[]
p=copy.deepcopy(original);p['data']['comparisons'][0]['subgroup_comparison']['score']=1.1;neg.append(p)
p=copy.deepcopy(original);del p['data']['comparisons'][0]['subgroup_comparison']['profile_level'];neg.append(p)
p=copy.deepcopy(original);p['surprise']='field';neg.append(p)
p=copy.deepcopy(original);p['data_mode']='real-ish';neg.append(p)
for p in neg:assert list(validator.iter_errors(p))
# The full/backend bundle also includes the master acceptance matrix.
master_path=R/'01_MASTER_PLAN_V2.2.md'
if master_path.exists():
 master=master_path.read_text()
 for n in range(1,39):assert '| T%02d |'%n in master
print(json.dumps({'openapi':'valid 3.1.0','schemas':len(api['components']['schemas']),'fixtures':len(responses),'resolved_reference_occurrences':refs,'verified_quote_occurrences':quotes,'negative_cases_rejected':len(neg)},indent=2))
