import { useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Card, Spin } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { getBotConfig, saveBotConfig } from '../api/user';
import { ChatAvatar } from './ChatAvatar';
import { avatarColors, prepareAvatar } from './avatarImage';

export default function AvatarSettings({ onDirtyChange }: { onDirtyChange: (dirty: boolean) => void }) {
  const [values, setValues] = useState({ userAvatar: '', botAvatar: '' });
  const [saved, setSaved] = useState(values);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [processing, setProcessing] = useState(false);
  const uploadVersion = useRef(0);
  const { message } = App.useApp();
  const dirty = values.userAvatar !== saved.userAvatar || values.botAvatar !== saved.botAvatar;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  const load = async () => {
    setLoading(true); setFailed(false);
    try {
      const { data } = await getBotConfig();
      const next = { userAvatar: data?.userAvatar || '', botAvatar: data?.botAvatar || '' };
      setValues(next); setSaved(next);
    } catch { setFailed(true); } finally { setLoading(false); }
  };
  useEffect(() => { void load(); const version = uploadVersion; return () => { version.current++; }; }, []);
  const save = async () => {
    setSaving(true);
    try {
      await saveBotConfig(values); setSaved(values);
      window.dispatchEvent(new Event('xiaozhi:avatars-updated'));
      message.success('头像已保存，对话记录将使用新头像');
    } catch { message.error('头像保存失败，请重试'); } finally { setSaving(false); }
  };
  return <Card className="settings-card avatar-settings" title="对话头像">
    <p className="avatar-description">给你和小智选一个头像，让每段对话更有熟悉感。头像用于当前账号的所有对话。</p>
    {loading ? <Spin /> : failed ? <Alert type="error" title="头像配置加载失败" action={<Button onClick={load}>重试</Button>} /> : <>
      <div className="avatar-editors">{(['userAvatar', 'botAvatar'] as const).map(key => <section key={key} className="avatar-editor">
        <ChatAvatar value={values[key]} robot={key === 'botAvatar'} size={76} />
        <div className="avatar-editor-options"><h3>{key === 'userAvatar' ? '我的头像' : '小智的头像'}</h3>
          <div className="avatar-swatches">{avatarColors.map((color, i) => <button key={color} type="button" disabled={saving || processing} aria-label={`${key === 'userAvatar' ? '本人' : '机器人'}头像：${['晴蓝', '薄荷', '蜜桃', '浅紫'][i]}`} aria-pressed={values[key] === `preset:${color}`} onClick={() => setValues(v => ({ ...v, [key]: `preset:${color}` }))}><ChatAvatar value={`preset:${color}`} robot={key === 'botAvatar'} size={30} /></button>)}</div>
          <div className="avatar-actions"><label className={`avatar-upload ${saving || processing ? 'disabled' : ''}`}>
            <UploadOutlined /> 上传图片<input type="file" accept="image/png,image/jpeg,image/webp" disabled={saving || processing} aria-label={`上传${key === 'userAvatar' ? '本人' : '机器人'}头像`} onChange={async e => {
              const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
              const version = ++uploadVersion.current; setProcessing(true);
              try { const value = await prepareAvatar(file); if (version === uploadVersion.current) setValues(v => ({ ...v, [key]: value })); }
              catch (err) { message.error(err instanceof Error ? err.message : '无法读取图片'); }
              finally { if (version === uploadVersion.current) setProcessing(false); }
            }} /></label><Button type="text" size="small" disabled={saving || processing} onClick={() => setValues(v => ({ ...v, [key]: '' }))}>恢复默认</Button></div>
        </div>
      </section>)}</div>
      <p className="avatar-hint">支持 JPG、PNG、WebP，最大 5 MB；图片将自动居中裁成方形。</p>
      <div className="avatar-chat-preview" aria-label="头像对话预览"><div><ChatAvatar value={values.botAvatar} robot /><span>今天有什么想分享的？</span></div><div><ChatAvatar value={values.userAvatar} /><span>来和你聊聊天。</span></div></div>
      <Button type="primary" loading={saving} disabled={!dirty || processing} onClick={save}>保存头像</Button>
      {dirty && <span className="avatar-hint"> 有未保存的头像修改</span>}
    </>}
  </Card>;
}
