// Inert local PNG sequence -> silent MP4/GIF. No user code, URLs or ffmpeg args.
import fs from 'node:fs';
import path from 'node:path';
import {stitchFramesToVideo, RenderInternals, makeCancelSignal} from '@remotion/renderer';
const [directory]=process.argv.slice(2);
if (!directory || !path.isAbsolute(directory)) throw new Error('Private job directory required');
const spec=JSON.parse(fs.readFileSync(path.join(directory,'encode.json'),'utf8'));
const {width,height,frames,fps,format}=spec;
if (![12,24].includes(fps) || !['mp4','gif'].includes(format) || !Number.isInteger(frames) || frames<2 || frames>120 ||
    ![width,height].every(x=>Number.isInteger(x)&&x>=2&&x<=(format==='gif'?480:720)&&x%2===0) ||
    width*height*frames>(format==='gif'?28000000:64000000)) throw new Error('Invalid encoding bounds');
for (let i=0;i<frames;i++) {
  const f=path.join(directory,`frame-${String(i).padStart(4,'0')}.png`);
  if (!fs.lstatSync(f).isFile() || fs.lstatSync(f).isSymbolicLink()) throw new Error('Missing private PNG');
}
const downloadMap=RenderInternals.makeDownloadMap(48000);
const {cancelSignal,cancel}=makeCancelSignal();
process.on('SIGTERM',()=>cancel());
await stitchFramesToVideo({
  assetsInfo:{assets:[],imageSequenceName:path.join(directory,'frame-%04d.png'),firstFrameIndex:0,downloadMap,
    chunkLengthInSeconds:frames/fps,trimLeftOffset:0,trimRightOffset:0,forSeamlessAacConcatenation:false},
  width,height,fps,codec:format==='mp4'?'h264':'gif',muted:true,enforceAudioTrack:false,
  outputLocation:path.join(directory,`output.${format}`),pixelFormat:'yuv420p',hardwareAcceleration:'disable',
  ...(format==='mp4'?{x264Preset:'veryfast',crf:20}:{}),numberOfGifLoops:0,cancelSignal,
  ffmpegOverride:({args})=>['-threads','2',...args,'-threads','2'],
});
