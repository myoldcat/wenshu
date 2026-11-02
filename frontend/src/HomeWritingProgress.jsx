import React from 'react';
import { BellRinging } from '@phosphor-icons/react';
import { isWritingProjectActive, writingProjectCardProgress, writingProjectCardStatus, writingProjectPhase } from './writing-projects';
import './home-writing-progress.css';

export default function HomeWritingProgress({ project = {} }) {
  const { ratio, percent, completed, total, stageValues, stageIds, stageWeights, stageStates } = writingProjectCardProgress(project);
  const active = isWritingProjectActive(project);
  const phase = writingProjectPhase(project);
  const status = writingProjectCardStatus(project);
  const pending = (project.runs || []).some(run => ['queued', 'running', 'paused'].includes(run.status))
    || ['queued', 'running', 'paused'].includes(project.automatic?.status);
  const complete = ratio >= 1 || phase.id === 'ready';
  const waiting = !pending && !complete && (
    phase.id === 'outline' && status === '待确认纲要'
      && total > 0 && !project.outlineConfirmed
    || phase.id === 'images' && status === '待确认配图'
      && total > 0 && completed === total
  );
  const flowing = active && ratio > 0;
  const description = `整体 ${Math.round(percent)}%，${status}；${total
    ? `正文 ${completed} / ${total} 章已有正文` : '尚未拟定纲要'}`;

  return <span className="home-manuscript-progress home-writing-progress" title={description}
    data-phase={phase.id} data-motion={flowing ? 'flow' : 'point'}
    data-waiting={waiting ? 'true' : undefined}
    style={{ '--home-writing-progress-position': `${ratio * 100}%` }}>
    <progress value={percent} max={100} aria-label="写作进度" aria-valuetext={description}/>
    <span className="home-writing-progress-track" aria-hidden="true">
      <span className="home-writing-progress-spectrum" data-steps={stageIds.length}
        style={{ gridTemplateColumns: stageWeights.map(weight => `${weight}fr`).join(' ') }}>
        {stageValues.map((value, index) => <span className="home-writing-progress-segment"
          key={stageIds[index]} data-stage={stageIds[index]} data-state={stageStates[index]}>
          <i style={{ width: `${value * 100}%` }}/>
        </span>)}
      </span>
      {flowing && <span className="home-writing-progress-light"><i/></span>}
      {ratio < 1 && <span className={waiting
        ? 'home-writing-progress-notice' : 'home-writing-progress-point'}>
        {waiting && <BellRinging size={13} weight="duotone"/>}
      </span>}
    </span>
  </span>;
}
