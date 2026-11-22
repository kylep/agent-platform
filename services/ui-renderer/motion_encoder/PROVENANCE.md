Encoder integration reuses claude-video's pinned Remotion 4.0.539 renderer and
bundled FFmpeg distribution. Source toolkit commit:
6e8f2b761bf0305cf3f9f552c7f4f30862ef7a1f (kylep/claude-video).
Only stitchFramesToVideo is used; no browser, narration, fonts, remote assets,
React composition, user code or arbitrary encoder options are accepted.
The version-specific assetsInfo adapter is covered by real MP4/GIF tests.
See LICENSE-Remotion.md and installed compositor package licenses for terms.
