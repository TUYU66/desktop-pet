import { useState, useEffect, useCallback } from 'react';
import { Alert, Card, Input, Button, App, Spin, Form, Select, Space, Typography } from 'antd';
import { getBotConfig, saveBotConfig } from '../../api/user';
import useUnsavedChanges from '../../hooks/useUnsavedChanges';
import { RobotOutlined, SoundOutlined } from '@ant-design/icons';
import VolumeControl from './VolumeControl';
import WakeWordSettings from './WakeWordSettings';
import AvatarSettings from '../../components/AvatarSettings';
import './settings.css';

interface RoleItem {
  id: string;
  name: string;
  prompt: string;
  style?: string;
  userName?: string;
  wakeWords?: string;
  voice?: string;
}
interface ProfileForm { name: string; style: string; userName: string; voice: string }

const VOICE_GROUPS = [
  {
    label: '普通话',
    options: [
      { value: 'zh-CN-XiaoxiaoNeural', label: '晓晓 — 温柔女声（默认）' },
      { value: 'zh-CN-XiaoyiNeural', label: '晓伊 — 轻快女声' },
      { value: 'zh-CN-YunjianNeural', label: '云健 — 沉稳男声' },
      { value: 'zh-CN-YunxiNeural', label: '云希 — 阳光男声' },
      { value: 'zh-CN-YunyangNeural', label: '云扬 — 新闻男声' },
    ],
  },
  {
    label: '方言',
    options: [
      { value: 'zh-CN-shaanxi-XiaoniNeural', label: '陕西话' },
      { value: 'zh-TW-HsiaoChenNeural', label: '台湾国语（女）' },
      { value: 'zh-TW-YunJheNeural', label: '台湾国语（男）' },
      { value: 'zh-HK-HiuGaaiNeural', label: '粤语（女）' },
      { value: 'zh-HK-WanLungNeural', label: '粤语（男）' },
    ],
  },
  {
    label: '外语',
    options: [
      { value: 'en-US-JennyNeural', label: '美式英语（女）' },
      { value: 'en-US-GuyNeural', label: '美式英语（男）' },
      { value: 'en-GB-SoniaNeural', label: '英式英语（女）' },
      { value: 'en-GB-RyanNeural', label: '英式英语（男）' },
      { value: 'ja-JP-NanamiNeural', label: '日语（女）' },
      { value: 'ja-JP-KeitaNeural', label: '日语（男）' },
      { value: 'ko-KR-SunHiNeural', label: '韩语（女）' },
      { value: 'ko-KR-InJoonNeural', label: '韩语（男）' },
    ],
  },
];

export default function Settings() {
  const [form] = Form.useForm<ProfileForm>();
  const previewName = Form.useWatch('name', form);
  const previewUser = Form.useWatch('userName', form);
  const previewVoice = Form.useWatch('voice', form);
  const voiceLabel = VOICE_GROUPS.flatMap(group => group.options).find(voice => voice.value === previewVoice)?.label;
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [avatarDirty, setAvatarDirty] = useState(false);
  const [wakeDirty, setWakeDirty] = useState(false);
  const [previewWakeWord, setPreviewWakeWord] = useState<string | null>(null);
  useUnsavedChanges(dirty || avatarDirty || wakeDirty);
  const [loadFailed, setLoadFailed] = useState(false);
  const [roles, setRoles] = useState<RoleItem[]>([]);
  const [roleId, setRoleId] = useState('default');
  const { message } = App.useApp();

  const load = useCallback(async () => {
    setLoading(true);
    setLoadFailed(false);
    try {
      const res = await getBotConfig();
      const config = res.data;
      if (!config || typeof config !== 'object') throw new Error('角色配置未完整返回');
      const parsed: RoleItem[] = config.roles ? JSON.parse(config.roles) : [];
      if (!Array.isArray(parsed) || parsed.some(role => !role || typeof role !== 'object')) throw new Error('角色配置格式错误');
      const active = parsed.find(role => role.id === config.activeRole) || parsed[0];
      const configuredName = active ? active.name : config.name;
      setRoles(parsed);
      setRoleId(active?.id || config.activeRole || 'default');
      setPreviewWakeWord(config.customWakeWord || '');
      form.setFieldsValue({
        name: typeof configuredName === 'string' && configuredName.trim() ? configuredName.trim() : '桌面宠物',
        style: active?.style ?? active?.prompt ?? config.prompt ?? '',
        userName: active?.userName || '',
        voice: active?.voice || config.voice || 'zh-CN-XiaoxiaoNeural',
      });
    } catch {
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [form]);
  useEffect(() => { void load(); }, [load]);

  const save = async (values: ProfileForm) => {
    if (loading || loadFailed || saving) return;
    setSaving(true);
    try {
      const role: RoleItem = {
        id: roleId, name: values.name.trim(), style: values.style.trim(),
        userName: values.userName?.trim() || '', voice: values.voice, wakeWords: '你好小智',
        prompt: [values.style.trim(), `你的名字是${values.name.trim()}。`,
          values.userName?.trim() ? `请称呼用户为${values.userName.trim()}。` : ''].filter(Boolean).join('\n'),
      };
      // 保留旧配置用于历史兼容，只开放当前角色编辑。
      const updated = roles.some(item => item.id === roleId)
        ? roles.map(item => item.id === roleId ? role : item) : [...roles, role];
      await saveBotConfig({ roles: JSON.stringify(updated), activeRole: roleId,
        prompt: role.prompt, voice: values.voice, wakeWords: '你好小智' });
      setRoles(updated);
      setDirty(false);
      message.success('配置已保存，当前交互结束后应用；切换角色后需重新唤醒');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  return <div className="settings-page page-width">
    <div className="page-heading"><div><h1>桌面宠物设置</h1></div></div>
    <Typography.Paragraph type="secondary">设置伙伴的名字、说话方式、唤醒词和设备音量。名字与唤醒词分别设置。</Typography.Paragraph>
    {loading && <Spin />}
    {loadFailed && <Alert type="error" title="无法读取配置"
      description="请检查管理后端是否启动。加载成功后才能编辑，避免覆盖原有配置。"
      action={<Button onClick={load}>重新加载</Button>} />}
      <div className="settings-columns"><Card className="settings-card" title="角色配置" style={{ display: loading || loadFailed ? 'none' : undefined }}>
        <Form form={form} layout="vertical" onFinish={save} disabled={saving} onValuesChange={() => setDirty(true)} scrollToFirstError autoComplete="off">
          <Form.Item name="name" label="机器人名字" rules={[{ required: true, whitespace: true, message: '请输入名字' }]}>
            <Input autoComplete="off" maxLength={30} />
          </Form.Item>
          <Form.Item name="userName" label="如何称呼你"><Input autoComplete="off" maxLength={30} placeholder="例如：小林" /></Form.Item>
          <Form.Item name="style" label="性格与说话风格" rules={[{ required: true, whitespace: true, message: '请描述说话风格' }]}>
            <Input.TextArea rows={5} maxLength={6000} showCount placeholder="例如：温和、自然，回答简洁，愿意听我分享日常。" />
          </Form.Item>
          <Form.Item name="voice" label="音色"><Select options={VOICE_GROUPS} /></Form.Item>
          <Space wrap><Button type="primary" htmlType="submit" loading={saving}>保存配置</Button>
            <Typography.Text type="secondary">{dirty ? '有未保存的修改' : '保存角色设置不会清除聊天记录。'}</Typography.Text></Space>
        </Form>
        <div className="settings-inline-grid">
          <WakeWordSettings onDirtyChange={setWakeDirty} onWordChange={setPreviewWakeWord} />
          <VolumeControl />
        </div>
      </Card>
      <aside className="settings-preview" hidden={loading || loadFailed}>
        <div className="preview-header"><span className="preview-mascot" aria-hidden="true"><RobotOutlined /></span><span className="preview-label">配置概览{dirty || wakeDirty ? ' · 未保存' : ''}</span></div>
        <h2>{previewName?.trim() || '桌面宠物'}</h2>
        <dl className="preview-facts">
          <div><dt>称呼你</dt><dd>{previewUser?.trim() || '未设置'}</dd></div>
          <div><dt>音色</dt><dd><SoundOutlined /> {voiceLabel || previewVoice || '未选择'}</dd></div>
          <div><dt>唤醒词</dt><dd>{previewWakeWord === null ? '读取中' : previewWakeWord ? `${previewWakeWord} / 你好小智` : '你好小智'}</dd></div>
        </dl>
      </aside></div>
    <AvatarSettings onDirtyChange={setAvatarDirty} />
  </div>;
}
