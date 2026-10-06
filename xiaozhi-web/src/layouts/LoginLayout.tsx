import { Outlet } from 'react-router-dom';
import { RobotOutlined, MessageOutlined, SoundOutlined, SettingOutlined } from '@ant-design/icons';

export default function LoginLayout() {
  return <main className="login-scene">
    <section className="login-welcome">
      <div className="login-brand"><RobotOutlined /><strong>桌面宠物</strong></div>
      <div className="login-robot" aria-hidden="true"><div className="login-orbit" /><div className="robot-display"><i /><i /><span /></div></div>
      <h1>桌上的小伙伴，<br />随时聊几句。</h1><p>让每一次对话，都有声音回应。</p>
      <ul className="login-features"><li><MessageOutlined />回看对话</li><li><SoundOutlined />语音回复</li><li><SettingOutlined />自定义角色</li></ul>
    </section>
    <section className="login-form-panel" aria-label="账号登录与注册"><h2>欢迎回来</h2><p>登录，进入你的桌面宠物空间。</p><Outlet /><footer>基于 <a href="https://github.com/78/xiaozhi-esp32" target="_blank" rel="noreferrer">xiaozhi-esp32</a> 开源项目</footer></section>
  </main>;
}
