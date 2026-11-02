import React, { useEffect, useRef, useState } from 'react';
import './home-water-ripples.css';

const GROUPS = [
  { name: 'wide', duration: 15, offset: 1.5 },
  { name: 'middle', duration: 13, offset: 4 },
  { name: 'small', duration: 17, offset: 7 },
];

export default function HomeWaterRipples({ reduced = false, onMotionChange }) {
  const host = useRef(null);
  const [paused, setPaused] = useState(() => reduced || document.hidden);

  useEffect(() => {
    const node = host.current;
    const bounds = node.getBoundingClientRect();
    let inView = bounds.bottom > 0 && bounds.top < window.innerHeight
      && bounds.right > 0 && bounds.left < window.innerWidth;
    const update = () => {
      const next = reduced || document.hidden || !inView;
      setPaused(next);
      onMotionChange?.(next);
    };
    const observer = new IntersectionObserver(entries => {
      inView = entries[0]?.isIntersecting ?? false;
      update();
    });
    observer.observe(node);
    document.addEventListener('visibilitychange', update);
    update();
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', update);
      onMotionChange?.(true);
    };
  }, [reduced, onMotionChange]);

  return <div className="home-water-ripples" ref={host}
    data-paused={paused ? 'true' : undefined}
    data-reduced={reduced ? 'true' : undefined} aria-hidden="true">
    {GROUPS.map(group => <div className={`home-water-ripple-group is-${group.name}`} key={group.name}>
      {[0, 1, 2].map(index => <span className="home-water-ripple" key={index} style={{
        '--ripple-duration': `${group.duration}s`,
        '--ripple-delay': `${-group.offset - index * group.duration / 3}s`,
        '--ripple-rest-scale': .52 + index * .29,
        '--ripple-rest-opacity': [.48, .65, .38][index],
      }}/>) }
    </div>)}
  </div>;
}
