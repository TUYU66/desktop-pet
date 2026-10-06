import { useState } from 'react';
import { UserOutlined } from '@ant-design/icons';
import './avatars.css';

import { avatarColors } from './avatarImage';
export function ChatAvatar({ value = '', robot = false, size = 42 }: { value?: string; robot?: boolean; size?: number }) {
  const [failed, setFailed] = useState('');
  const image = /^data:image\/(webp|png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(value) && value.length <= 60000;
  const color = avatarColors.find(c => value === `preset:${c}`) || (robot ? 'blue' : 'peach');
  return <span className={`chat-avatar avatar-${color}`} style={{ width: size, height: size, fontSize: size * .46 }} aria-label={robot ? '机器人头像' : '本人头像'}>
    {image && failed !== value ? <img src={value} alt={robot ? '机器人头像' : '本人头像'} onError={() => setFailed(value)} />
      : robot ? <span className="avatar-robot-face" aria-hidden="true"><i /><i /><b /></span> : <UserOutlined />}
  </span>;
}

