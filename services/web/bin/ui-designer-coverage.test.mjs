import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateDesignerCoverage} from './ui-designer-coverage.mjs';
const exports=new Map([['Button',['@ap/ui/button']]]);
const index={entries:{'ui-button--default':{type:'story'}}};
const component={name:'Button',import_path:'@ap/ui/button',story_id:'ui-button--default',variants:[{id:'default',selection:{style:'primary'},props:{children:'Save'},width:70,height:32}]};
test('requires every public React component in Designer and a valid implementation mapping',()=>{
  assert.equal(validateDesignerCoverage({id:'agent-platform',revision:1,components:[component]},exports,index).coveredComponents,1);
  assert.match(validateDesignerCoverage({id:'agent-platform',revision:1,components:[]},exports,index).errors.join(),/missing from Designer/);
  const bad={...component,import_path:'@ap/ui/private',story_id:'missing',variants:[]};
  const result=validateDesignerCoverage({id:'agent-platform',revision:1,components:[bad]},exports,index);
  assert.equal(result.coveredComponents,0);
  assert.equal(result.errors.length,3);
});
