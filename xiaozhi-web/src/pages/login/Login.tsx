import { useState, useEffect, useCallback, useRef } from 'react';
import { Form, Input, Button, App, Tabs, Alert } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { login, register as registerApi, getRegistrationStatus } from '../../api/auth';
import type { RegistrationState } from '../../api/auth';
import { useUserStore } from '../../store/userStore';

export default function Login() {
  const [loginLoading, setLoginLoading] = useState(false);
  const [registerLoading, setRegisterLoading] = useState(false);
  const [registration, setRegistration] = useState<RegistrationState | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState('');
  const [activeTab, setActiveTab] = useState('register');
  const statusGeneration = useRef(0);
  const refreshRegistration = useCallback(async () => {
    const generation = ++statusGeneration.current;
    setStatusLoading(true);
    try {
      const response = await getRegistrationStatus();
      const state = response.data;
      if (!state || typeof state.initialized !== 'boolean' || typeof state.registrationOpen !== 'boolean' ||
          typeof state.accountConflict !== 'boolean' || (state.registrationOpen && (state.initialized || state.accountConflict))) {
        throw new Error('账户初始化状态无效');
      }
      if (generation === statusGeneration.current) { setRegistration(state); setStatusError(''); }
    } catch (error) {
      if (generation === statusGeneration.current) {
        setRegistration(null);
        setStatusError(error instanceof Error ? error.message : '无法读取账户初始化状态');
      }
    } finally {
      if (generation === statusGeneration.current) setStatusLoading(false);
    }
  }, []);
  useEffect(() => {
    void refreshRegistration();
    return () => { ++statusGeneration.current; };
  }, [refreshRegistration]);
  const { message } = App.useApp();
  const navigate = useNavigate();
  const setToken = useUserStore((s) => s.setToken);
  const setUser = useUserStore((s) => s.setUser);

  const handleLogin = async (values: any) => {
    setLoginLoading(true);
    try {
      const loginData: any = await login(values);
      setToken(loginData.data.token);
      setUser({
        id: loginData.data.userId,
        username: loginData.data.username,
        superAdmin: loginData.data.superAdmin,
      });
      message.success('登录成功');
      navigate('/');
    } catch (err: any) {
      const msg = err?.response?.data?.msg || err?.message || '登录失败';
      message.error(msg);
    } finally {
      setLoginLoading(false);
    }
  };

  const handleRegister = async (values: any) => {
    setRegisterLoading(true);
    try {
      await registerApi({ username: values.username, password: values.password });
      setRegistration({ initialized: true, registrationOpen: false, accountConflict: false });
      setActiveTab('login');
      message.success('账户初始化完成，请登录');
    } catch (err: any) {
      const msg = err?.response?.data?.msg || err?.message || '注册失败';
      message.error(msg);
    } finally {
      await refreshRegistration();
      setRegisterLoading(false);
    }
  };

  return (
    <>
    {statusLoading && <Alert type="info" title="正在获取账户初始化状态…" style={{ marginBottom: 16 }} />}
    {statusError && <Alert type="warning" showIcon title={statusError}
      description="暂时无法确认是否允许首次注册，你仍可使用已有账户登录。"
      action={<Button loading={statusLoading} onClick={() => void refreshRegistration()}>重试</Button>} style={{ marginBottom: 16 }} />}
    {registration?.accountConflict && <Alert type="error" showIcon title="检测到多个账户，单用户配置异常"
      description="注册已关闭，请联系维护者确认已有账户；系统不会自动删除账户。" style={{ marginBottom: 16 }} />}
    {registration?.registrationOpen && <Alert type="info" showIcon title="首次使用，请先创建唯一账户"
      description="创建成功后将关闭注册入口，此账户用于登录并管理你的机器人。" style={{ marginBottom: 16 }} />}
    {!registration?.accountConflict && <Tabs
      activeKey={registration?.registrationOpen ? activeTab : 'login'}
      onChange={setActiveTab}
      centered
      size="large"
      items={[
        {
          key: 'login',
          label: '登录',
          children: (
            <Form layout="vertical" onFinish={handleLogin} size="large" style={{ marginTop: 8 }}>
              <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
                <Input autoComplete="username" prefix={<UserOutlined style={{ color: '#7186a6' }} />} placeholder="用户名" />
              </Form.Item>
              <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
                <Input.Password autoComplete="current-password" prefix={<LockOutlined style={{ color: '#7186a6' }} />} placeholder="密码" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" block loading={loginLoading} size="large">
                  登录
                </Button>
              </Form.Item>
            </Form>
          ),
        },
        ...(registration?.registrationOpen ? [{
          key: 'register',
          label: '首次注册',
          children: (
            <Form layout="vertical" onFinish={handleRegister} size="large" style={{ marginTop: 8 }}>
              <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
                <Input autoComplete="username" prefix={<UserOutlined style={{ color: '#7186a6' }} />} placeholder="用户名" />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                rules={[{ required: true, min: 6, message: '密码至少6个字符' }]}
              >
                <Input.Password autoComplete="new-password"
                  prefix={<LockOutlined style={{ color: '#7186a6' }} />}
                  placeholder="密码（至少6位）"
                />
              </Form.Item>
              <Form.Item
                name="confirmPassword"
                label="确认密码"
                dependencies={['password']}
                rules={[
                  { required: true, message: '请确认密码' },
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      if (!value || getFieldValue('password') === value) return Promise.resolve();
                      return Promise.reject(new Error('两次密码不一致'));
                    },
                  }),
                ]}
              >
                <Input.Password autoComplete="new-password"
                  prefix={<LockOutlined style={{ color: '#7186a6' }} />}
                  placeholder="确认密码"
                />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" block loading={registerLoading} size="large">
                  创建唯一账户
                </Button>
              </Form.Item>
            </Form>
          ),
        }] : []),
      ]}
    />}
    </>
  );
}
