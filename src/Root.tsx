import React from 'react';
import {Composition} from 'remotion';
import {Video} from './Video';
import {scenes, SCENE_FRAMES} from './content';

export const Root: React.FC = () => (
  <Composition
    id="Video"
    component={Video}
    durationInFrames={scenes.length * SCENE_FRAMES}
    fps={30}
    width={1920}
    height={1080}
  />
);
