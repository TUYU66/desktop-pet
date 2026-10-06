import { useId } from 'react';

type Pose = readonly [number, number, number, number, number, number, number, number];
type Face = { pose: Pose; mood?: 'tired' | 'angry'; effect?: string; gaze?: number };

// Match RoboEyes::FindExpression in main/display/robo_eyes/robo_eyes.cc.
// Heights, radius, smile lid, mouth curve/opening/width, eye width.
const faces: Record<string, Face> = {
  neutral: { pose: [1,1,.33,0,.018,0,.13,1] },
  happy: { pose: [.85,.85,.45,.42,.055,0,.18,1], effect: 'stars' },
  curious: { pose: [1.08,.75,.38,0,0,.30,.09,.95], effect: 'question' },
  surprised: { pose: [1.15,1.15,.50,0,0,.70,.09,.80], effect: 'rays' },
  loving: { pose: [.55,.55,.45,.18,.035,0,.15,1.05], effect: 'hearts' },
  laughing: { pose: [.90,.90,.48,.38,.045,.65,.19,1], effect: 'stars' },
  peek_left: { pose: [1,1,.50,0,.018,0,.13,.88], effect: 'peek', gaze: -1 },
  peek_right: { pose: [1,1,.50,0,.018,0,.13,.88], effect: 'peek', gaze: 1 },
  daydream: { pose: [.60,.60,.18,0,0,0,.08,1], effect: 'daydream', gaze: 2 },
  sleepy: { pose: [.45,.45,.35,0,0,.20,.10,1], effect: 'sleep' },
  yawn: { pose: [.06,.06,.40,0,0,1,.14,1], effect: 'breath' },
  shy: { pose: [.85,.85,.45,.35,.04,0,.15,1], effect: 'blush' },
  sad: { pose: [1,1,.33,0,-.035,0,.13,1], mood: 'tired', effect: 'tears' },
  crying: { pose: [1,1,.33,0,-.05,.3,.13,1], mood: 'tired', effect: 'tears' },
  tired: { pose: [1,1,.33,0,0,0,.13,1], mood: 'tired', effect: 'breath' },
  angry: { pose: [1,1,.25,0,0,0,.13,1], mood: 'angry', effect: 'angry' },
  confused: { pose: [1,.8,.33,0,0,.2,.12,1], effect: 'question' },
  winking: { pose: [.08,1,.33,0,.04,0,.15,1], effect: 'stars' },
  confident: { pose: [1,1,.33,.2,.045,0,.16,1], effect: 'rays' },
  listening: { pose: [1,1,.33,0,0,.18,.13,1] },
  thinking: { pose: [1,1,.33,0,0,.18,.13,1], effect: 'question' },
  singing: { pose: [1,1,.33,.4,.055,0,.18,1], effect: 'music' },
  reminder_ack: { pose: [1,1,.33,.4,.055,0,.18,1], effect: 'confetti' },
  reminder_call: { pose: [1,1,.33,.4,.055,0,.18,1], effect: 'rays' },
  reminder_wait: { pose: [1,1,.33,0,.018,0,.13,1] },
};
const aliases: Record<string, string> = {
  relaxed: 'neutral', speaking: 'neutral', funny: 'laughing', silly: 'laughing',
  embarrassed: 'shy', shocked: 'surprised', closed: 'sleepy', cool: 'confident',
  delicious: 'loving', kissy: 'loving',
};
const width = 240;
const height = 160;
const eyeWidth = width * .26;
const eyeHeight = Math.min(height * .40, width * .23);
const eyeY = height * .40;
const mouthY = height * .78;
const stroke = Math.max(2, width / 90);

function mouthContour(pose: Pose) {
  const points: string[] = [];
  for (const side of [-1, 1]) {
    for (let step = 0; step <= 32; step++) {
      const t = side === -1 ? step / 16 - 1 : 1 - step / 16;
      const x = width / 2 + t * width * pose[6] / 2;
      const y = mouthY + height * pose[4] * (1 - t * t)
        + side * (height * .065 * pose[5] * Math.sqrt(Math.max(0, 1 - t * t)) + stroke / 2);
      points.push(`${points.length ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`);
    }
  }
  return points.join(' ') + ' Z';
}

function FaceEffect({ effect, gaze, color }: { effect?: string; gaze?: number; color: string }) {
  switch (effect) {
    case 'hearts': return <g fill="#ff6c9c">{[[-94,-12],[94,-24],[86,18]].map(([x,y], i)=><path key={i} transform={`translate(${120+x} ${eyeY+y}) scale(.55)`} d="M0 4 C-16 -7 -18 -17 -9 -19 C-4 -20 0 -17 0 -13 C0 -17 4 -20 9 -19 C18 -17 16 -7 0 4Z"/>)}</g>;
    case 'stars': return <g stroke="#ffdd66" strokeWidth="1.6" strokeLinecap="round">{[[-100,-30],[-100,30],[100,-30],[100,30]].map(([x,y], i)=><path key={i} d={`M${120+x-4} ${eyeY+y}h8 M${120+x} ${eyeY+y-4}v8`}/>)}</g>;
    case 'question': return <g stroke="#ffd782" strokeWidth="2" fill="none" strokeLinecap="round"><path d="M198 24 C198 16 210 16 210 23 C210 29 204 28 204 34"/><circle cx="204" cy="40" r="1" fill="#ffd782"/></g>;
    case 'rays': return <g stroke="#ffdf84" strokeWidth="1.6" strokeLinecap="round">{[-1,1].flatMap(side=>[-1,0,1].map(i=><path key={`${side}:${i}`} d={`M${120+side*94} ${eyeY+i*10}l${side*6} ${i*3}`}/>))}</g>;
    case 'peek': return <path d={`M${120+(gaze||1)*100-(gaze||1)*4} 38 l${(gaze||1)*4} 4 l${-(gaze||1)*4} 4`} stroke={color} strokeWidth="1.5" fill="none"/>;
    case 'daydream': return <g fill="#b9bdff" opacity=".7">{[145,151,157].map(x=><circle key={x} cx={x} cy="24" r="1.3"/>)}</g>;
    case 'sleep': return <g stroke={color} strokeWidth="1.5" fill="none"><path d="M174 26h7l-7 7h7 M192 12h9l-9 9h9"/></g>;
    case 'blush': return <g stroke="#ff819a" strokeWidth="1.8" strokeLinecap="round">{[-1,1].flatMap(side=>[-1,0,1].map(i=><path key={`${side}:${i}`} d={`M${120+side*87+i*5} 98l2 5`}/>))}</g>;
    case 'tears': return <g fill="#82cfff">{[52,188].map(x=><path key={x} d={`M${x} 96q-6 8 0 9q6-1 0-9Z`}/>)}</g>;
    case 'breath': return <g fill="none" stroke="#c3e4ff" strokeWidth="1" opacity=".65"><circle cx="182" cy="114" r="3"/><circle cx="195" cy="102" r="4"/></g>;
    case 'angry': return <path d="M170 8v12 M176 8v12 M167 11h12 M167 17h12" stroke="#ff6060" strokeWidth="2"/>;
    case 'music': return <g fill={color}><path d="M201 29v18a4 3 0 1 1-2-3V28l13-3v17a4 3 0 1 1-2-3V28Z"/></g>;
    case 'confetti': return <g>{[[-75,19],[-94,38],[82,22],[99,39],[-28,10],[32,11]].map(([x,y],i)=><rect key={i} x={120+x} y={y} width="3" height="4" fill={['#ff8eab','#ffdb66','#76e8cc','#9ba7ff'][i%4]}/>)}</g>;
    default: return null;
  }
}

type Props = { expression: string | null; label: string; eyeColor: string; mouthColor: string; speaking: boolean; animate: boolean };

export default function FacePreview({ expression, label, eyeColor, mouthColor, speaking, animate }: Props) {
  const id = useId().replace(/:/g, '');
  const face = expression ? faces[aliases[expression] || expression] : undefined;
  if (!face) return <div className="lcd-face-screen lcd-face-empty">{expression ? '当前表情暂不支持预览' : '等待设备上报表情'}</div>;
  const { pose, mood, effect, gaze } = face;
  const talking = speaking || expression === 'singing';
  return <div className={`lcd-face-screen${animate ? ' lcd-face-animated' : ''}`}>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label}，眼睛和嘴巴的 LCD 预览`}>
      <g className={expression === 'winking' || expression === 'yawn' ? undefined : 'lcd-face-eyes'}>
        {[0,1].map(side=>{
          const cx = width / 2 + (side ? 1 : -1) * (eyeWidth + width * .12) / 2;
          const w = eyeWidth * pose[7];
          const h = Math.max(2, eyeHeight * pose[side]);
          const x = cx - w / 2;
          const y = eyeY - h / 2;
          const r = Math.min(w * pose[2] * h / eyeHeight, w / 2, h / 2);
          const cutX = (mood === 'angry') === (side === 0) ? x + w : x;
          return <g key={side}>
            <defs><clipPath id={`${id}-eye-${side}`}><rect x={x} y={y} width={w} height={h} rx={r}/></clipPath></defs>
            <g clipPath={`url(#${id}-eye-${side})`}>
              <rect x={x} y={y} width={w} height={h} fill={eyeColor}/>
              {pose[3]>0&&<rect x={x} y={y+h*(1-pose[3])} width={w} height={h*pose[3]} fill="#000"/>}
              {mood&&<path d={`M${cx} ${y}H${cutX}V${y+h*.44}Z`} fill="#000"/>}
              {gaze&&<ellipse cx={cx+(gaze===2?.12:gaze)*w*.22} cy={eyeY+(gaze===2?-h*.18:0)} rx={h*.13} ry={h*.19} fill="#000"/>}
            </g>
          </g>;
        })}
      </g>
      {talking ? <rect className="lcd-face-speaking-mouth" x={width/2-width*pose[6]*.7/2} y={mouthY-6} width={width*pose[6]*.7} height="12" rx="6" fill={mouthColor}/> : <path d={mouthContour(pose)} fill={mouthColor} stroke={mouthColor} strokeWidth=".5" strokeLinejoin="round"/>}
      <FaceEffect effect={effect} gaze={gaze} color={eyeColor}/>
    </svg>
  </div>;
}
