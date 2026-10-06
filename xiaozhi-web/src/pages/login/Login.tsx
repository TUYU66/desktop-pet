import { useState } from 'react';
import { Form, Input, Button, App, Tabs } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { login, register as registerApi } from '../../api/auth';
import { useUserStore } from '../../store/userStore';

export default function Login() {
  const [loginLoading, setLoginLoading] = useState(false);
  const [registerLoading, setRegisterLoading] = useState(false);
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
      await registerApi(values);
      message.success('注册成功，请登录');
    } catch (err: any) {
      const msg = err?.response?.data?.msg || err?.message || '注册失败';
      message.error(msg);
    } finally {
      setRegisterLoading(false);
    }
  };

  return (
    <Tabs
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
        {
          key: 'register',
          label: '注册',
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
                  注册
                </Button>
              </Form.Item>
            </Form>
          ),
        },
      ]}
    />
  );
}
