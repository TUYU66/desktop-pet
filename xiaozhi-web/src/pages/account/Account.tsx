import { useState, useEffect } from 'react';
import { Card, Form, Input, Button, App, Descriptions, Spin, Alert } from 'antd';
import { getUserInfo, updatePassword } from '../../api/user';

export default function Account() {
  const [user, setUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [pwdForm] = Form.useForm();
  const [pwdLoading, setPwdLoading] = useState(false);
  const { message } = App.useApp();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const res: any = await getUserInfo();
        if (!cancelled) { setUser(res.data); setError(''); }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '账号信息读取失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [reload]);

  const handleChangePassword = async () => {
    setPwdLoading(true);
    try {
      const values = await pwdForm.validateFields();
      await updatePassword(values.currentPassword, values.newPassword);
      message.success('密码已修改');
      pwdForm.resetFields();
    } catch (err: any) {
      message.error(err?.response?.data?.msg || err?.message || '修改密码失败');
    } finally {
      setPwdLoading(false);
    }
  };

  if (loading) return <Spin style={{ display: 'block', margin: '120px auto' }} />;
  if (error) return <Alert type="error" showIcon title="账号信息读取失败" description={error}
    action={<Button onClick={() => setReload(value => value + 1)}>重试</Button>} />;

  return (
    <div className="utility-page account-page page-width">
      <div className="page-heading"><div><h1>账号管理</h1><p>查看账号信息，管理登录密码。</p></div></div>

      <Card
        style={{ marginBottom: 24, borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
        title={<span style={{ fontSize: 15, fontWeight: 600 }}>个人信息</span>}
      >
        <Descriptions column={{ xs: 1, sm: 2 }} bordered={false} colon={false} size="small">
          <Descriptions.Item label={<span style={{ color: '#607493' }}>用户名</span>}>
            {user?.username || '-'}
          </Descriptions.Item>
          <Descriptions.Item label={<span style={{ color: '#607493' }}>角色</span>}>
            {user?.superAdmin === 1 ? '超级管理员' : '普通用户'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        style={{ borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
        title={<span style={{ fontSize: 15, fontWeight: 600 }}>修改密码</span>}
      >
        <Form form={pwdForm} layout="vertical" style={{ maxWidth: 400 }}>
          <Form.Item name="currentPassword" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item name="newPassword" label="新密码" rules={[{ required: true, min: 6, message: '密码至少6位' }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="confirmPassword" label="确认密码" dependencies={['newPassword']} rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('newPassword') === value) return Promise.resolve();
                return Promise.reject(new Error('两次密码不一致'));
              },
            }),
          ]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={handleChangePassword} loading={pwdLoading}>修改密码</Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
