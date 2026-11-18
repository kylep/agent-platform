import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {publicComponents} from './ui-story-coverage.mjs';

export function validateDesignerCoverage(manifest, components, index) {
  const errors=[], seen=new Set();
  let coveredComponents=0, variants=0;
  if(manifest.id!=='agent-platform' || !Number.isSafeInteger(manifest.revision) || manifest.revision<1 || !Array.isArray(manifest.components)) {
    return {errors:['Invalid Designer library manifest'],totalComponents:components.size,coveredComponents:0,variants:0};
  }
  for(const component of manifest.components) {
    if(seen.has(component.name)) errors.push(`Duplicate Designer component: ${component.name}`);
    seen.add(component.name);
    const validExport=components.get(component.name)?.includes(component.import_path);
    if(!validExport) errors.push(`Unknown Designer implementation mapping: ${component.name} / ${component.import_path}`);
    if(index.entries[component.story_id]?.type!=='story') errors.push(`Designer ${component.name} links to missing Storybook story ${component.story_id}`);
    if(!component.variants?.length) errors.push(`Designer ${component.name} has no visual variants`);
    else {
      if(validExport) coveredComponents++;
      variants+=component.variants.length;
      const ids=new Set();
      for(const variant of component.variants) {
        if(!variant.id || ids.has(variant.id)) errors.push(`Designer ${component.name} has duplicate/empty variant ID`);
        ids.add(variant.id);
        if(!variant.selection || !Object.keys(variant.selection).length || !variant.props || !(variant.width>0 && variant.height>0)) errors.push(`Designer ${component.name}/${variant.id} has incomplete native/React mapping`);
      }
    }
  }
  for(const name of components.keys()) if(!seen.has(name)) errors.push(`Public component missing from Designer: ${name}`);
  return {errors,totalComponents:components.size,coveredComponents,variants};
}
export function checkDesignerCoverage(root) {
  const read=path=>JSON.parse(readFileSync(resolve(root,path),'utf8'));
  return validateDesignerCoverage(read('packages/ui/designer-library.json'),publicComponents(root),read('services/web/storybook-static/index.json'));
}
