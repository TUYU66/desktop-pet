import { Outlet, Link, useNavigate, useLocation } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { Layout, Menu, Button, Dropdown, Avatar, Badge } from 'antd';
import { CustomerServiceOutlined, HomeOutlined, SettingOutlined, CloudUploadOutlined, UserOutlined, MenuFoldOutlined, MenuUnfoldOutlined, LogoutOutlined, MessageOutlined, IdcardOutlined, RobotOutlined, BulbOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useUserStore } from '../store/userStore';
import { useAppStore } from '../store/appStore';
import { getReminders } from '../api/reminder';
import type { ReminderPage } from '../api/reminder';
import ReminderPopup from './ReminderPopup';
import { validPopupPage } from './reminderPopupState';
import request from '../api/request';

type BatteryState = { percent: number; millivolts: number; low: boolean };
export default function MainLayout() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const user = useUserStore(s => s.user);
  const logout = useUserStore(s => s.logout);
  const collapsed = useAppStore(s => s.collapsed);
  const toggleCollapsed = useAppStore(s => s.toggleCollapsed);
  const [battery, setBattery] = useState<BatteryState | null>(null);
  const [hasUnanswered, setHasUnanswered] = useState(false);
  const [reminderPage, setReminderPage] = useState<ReminderPage | null>(null);
  useEffect(() => {
    let mounted = true;
    let batteryBusy = false;
    let reminderBusy = false;
    const loadBattery = async () => {
      if (batteryBusy || document.hidden) return;
      batteryBusy = true;
      try {
        const result = await request.get('/devices/panel');
        const selected = result.data?.selected;
        const chassis = selected?.chassis;
        const percent = chassis?.batteryPercent;
        if (mounted && !document.hidden) setBattery(selected?.online && chassis?.connected && chassis?.statusValid && Number.isFinite(percent) && percent >= 0 && percent <= 100 && Number.isFinite(chassis.batteryMv) && chassis.batteryMv > 0
          ? { percent, millivolts: chassis.batteryMv, low: !!chassis.lowBattery } : null);
      } catch { if (mounted) setBattery(null); }
      finally { batteryBusy = false; }
    };
    const loadUnanswered = async () => {
      if (reminderBusy || document.hidden) return;
      reminderBusy = true;
      try {
        const result = await getReminders();
        const count = result.data?.unansweredCount;
        if (!validPopupPage(result.data)) throw new Error('提醒状态尚待核实');
        if (mounted && !document.hidden) { setHasUnanswered(count > 0); setReminderPage(result.data); }
      } catch { /* Keep the last confirmed badge while a read is unavailable. */ }
      finally { reminderBusy = false; }
    };
    const acceptReminderState = () => { void loadUnanswered(); };
    const refresh = () => { setBattery(null); if (!document.hidden) { void loadBattery(); void loadUnanswered(); } };
    refresh();
    const batteryTimer = window.setInterval(() => { void loadBattery(); }, 5000);
    const reminderTimer = window.setInterval(() => { void loadUnanswered(); }, 3000);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('xiaozhi:reminders-state', acceptReminderState);
    return () => {
      mounted = false;
      window.clearInterval(batteryTimer);
      window.clearInterval(reminderTimer);
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('xiaozhi:reminders-state', acceptReminderState);
    };
  }, []);
  const items = [
    { key: '/', icon: <HomeOutlined />, label: <Link to="/">概览</Link> },
    { key: '/chat', icon: <MessageOutlined />, label: <Link to="/chat">对话记录</Link> },
    { key: '/memory', icon: <BulbOutlined />, label: <Link to="/memory">长期记忆</Link> },
    { key: '/reminders', icon: <Badge dot={hasUnanswered} offset={[2, 0]}><ClockCircleOutlined /></Badge>, label: <Link to="/reminders">日程提醒</Link> },
    { key: '/music', icon: <CustomerServiceOutlined />, label: <Link to="/music">音乐空间</Link> },
    { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">桌面宠物设置</Link> },
  ];
  const canLeave = () => window.dispatchEvent(new Event('xiaozhi:before-leave', { cancelable: true }));
  const guardedNavigate = (path: string) => { if (canLeave()) navigate(path); };
  const accountItems = [
    { key: 'account', icon: <IdcardOutlined />, label: '账号管理', onClick: () => guardedNavigate('/account') },
    { key: 'ota', icon: <CloudUploadOutlined />, label: '固件管理', onClick: () => guardedNavigate('/ota') },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: () => { if (canLeave()) { logout(); navigate('/login'); } } },
  ];
  return <Layout className="app-shell"><a className="skip-link" href="#main-content">跳到主要内容</a>
    <ReminderPopup page={reminderPage} />
    <Layout.Sider className="app-sidebar" width={216} collapsed={collapsed} theme="light" trigger={null}>
      <div className="brand"><span className="brand-mark"><RobotOutlined /></span>{!collapsed && <div><strong>桌面宠物</strong><small>我的桌面伙伴</small></div>}</div>
      {!collapsed && <div className="nav-caption">日常空间</div>}
      <Menu mode="inline" selectedKeys={[pathname]} items={items} />
      {!collapsed && <div className="sidebar-note">聊聊天，也照顾好每一天。</div>}
    </Layout.Sider>
    <Layout style={{ marginLeft: collapsed ? 80 : 216, minWidth: 0 }}>
      <Layout.Header className="app-header">
        <div className="header-location"><Button type="text" aria-label={collapsed ? '展开导航' : '收起导航'} icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={toggleCollapsed} /><span>{{ '/': '概览', '/chat': '对话记录', '/memory': '长期记忆', '/reminders': '日程提醒', '/music': '音乐空间', '/settings': '桌面宠物设置', '/ota': '固件管理' }[pathname] || '账号与设备'}</span></div>
        <div className="header-actions"><div className={`header-battery${battery?.low ? ' is-low' : ''}`} title={battery ? `${battery.percent}% · ${(battery.millivolts / 1000).toFixed(2)} V（电压估算）` : '设备电量暂不可用'} aria-label={battery ? `当前电量 ${battery.percent}%` : '电量暂不可用'}><span className="header-battery-icon" aria-hidden="true">{battery && <span style={{ width: `${battery.percent}%` }} />}</span><span>{battery ? `${battery.percent}%` : '电量 --'}</span></div><Dropdown menu={{ items: accountItems }} placement="bottomRight" trigger={['click']}><Button type="text" className="account-button"><Avatar size={26} icon={<UserOutlined />} /><span>{user?.nickname || user?.username || '我的账号'}</span></Button></Dropdown></div>
      </Layout.Header>
      <Layout.Content id="main-content" tabIndex={-1} className="app-content"><div className="page-motion" key={pathname}><Outlet /></div></Layout.Content>
    </Layout>
  </Layout>;
}
