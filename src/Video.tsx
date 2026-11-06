import React from 'react';
import {AbsoluteFill, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {scenes, SCENE_FRAMES, Scene} from './content';

const SceneView: React.FC<Scene> = ({title, text, color}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 12}});
  const fadeOut = interpolate(frame, [SCENE_FRAMES - 12, SCENE_FRAMES], [1, 0], {
    extrapolateLeft: 'clamp',
  });
  const sub = interpolate(frame, [15, 35], [0, 1], {extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill
      style={{
        background: color,
        justifyContent: 'center',
        alignItems: 'center',
        fontFamily: 'Helvetica, Arial, sans-serif',
        color: 'white',
        opacity: fadeOut,
      }}
    >
      <h1 style={{fontSize: 160, margin: 0, transform: `scale(${pop})`}}>{title}</h1>
      <p style={{fontSize: 64, margin: 0, opacity: sub, transform: `translateY(${(1 - sub) * 30}px)`}}>
        {text}
      </p>
    </AbsoluteFill>
  );
};

export const Video: React.FC = () => (
  <AbsoluteFill>
    {scenes.map((s, i) => (
      <Sequence key={i} from={i * SCENE_FRAMES} durationInFrames={SCENE_FRAMES}>
        <SceneView {...s} />
      </Sequence>
    ))}
  </AbsoluteFill>
);
