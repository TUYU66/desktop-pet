import { useState, useEffect } from 'react';
import { Card, Descriptions, Spin, Statistic, Row, Col } from 'antd';
import { WifiOutlined, ThunderboltOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { getDeviceInfo } from '../../api/device';
import dayjs from 'dayjs';

export default function DeviceList() {
  const [device, setDevice] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchDevice = async () => {
    setLoading(true);
    try {
      const res: any = await getDeviceInfo();
      setDevice(res.data);
    } catch {
      setDevice(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchDevice(); }, []);

  if (loading) return <Spin style={{ display: 'block', margin: '120px auto' }} />;

  if (!device) {
    return (
      <Card
        style={{ borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)', textAlign: 'center', padding: 40 }}
      >
        <p style={{ color: '#607493', fontSize: 15 }}>
          暂无设备信息，请确认 ESP32 已连接服务器。
        </p>
      </Card>
    );
  }

  const online = device.status === 'online';

  return (
    <div className="utility-page page-width"><div className="page-heading"><div><h1>设备详情</h1><p>查看机器人上报的设备信息。</p></div></div>
      <Row gutter={[20, 20]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card
            style={{ borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
          >
            <Statistic
              title={<span style={{ fontSize: 13, color: '#607493' }}>在线状态</span>}
              value={online ? '在线' : '离线'}
              prefix={<WifiOutlined style={{ color: online ? '#10b981' : '#d1d5db', fontSize: 18 }} />}
              styles={{ value: { fontSize: 18, fontWeight: 600, color: online ? '#10b981' : '#9ca3af' } }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card
            style={{ borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
          >
            <Statistic
              title={<span style={{ fontSize: 13, color: '#607493' }}>电量</span>}
              value={device.battery != null ? `${device.battery}%` : '-'}
              prefix={<ThunderboltOutlined style={{ color: '#f59e0b', fontSize: 18 }} />}
              styles={{ value: { fontSize: 18, fontWeight: 600 } }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card
            style={{ borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
          >
            <Statistic
              title={<span style={{ fontSize: 13, color: '#607493' }}>固件版本</span>}
              value={device.deviceVersion || '-'}
              styles={{ value: { fontSize: 16, fontWeight: 600 } }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card
            style={{ borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
          >
            <Statistic
              title={<span style={{ fontSize: 13, color: '#607493' }}>最后在线</span>}
              value={device.lastOnlineTime ? dayjs(device.lastOnlineTime).format('MM-DD HH:mm') : '-'}
              prefix={<ClockCircleOutlined style={{ color: '#497acf', fontSize: 18 }} />}
              styles={{ value: { fontSize: 14, fontWeight: 500 } }}
            />
          </Card>
        </Col>
      </Row>

      <Card
        style={{ borderRadius: 16, border: 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
        title={<span style={{ fontSize: 15, fontWeight: 600 }}>设备详情</span>}
      >
        <Descriptions column={1} bordered={false} colon={false} size="small">
          <Descriptions.Item label={<span style={{ color: '#607493' }}>MAC 地址</span>}>
            {device.macAddress || '-'}
          </Descriptions.Item>
          <Descriptions.Item label={<span style={{ color: '#607493' }}>设备型号</span>}>
            {device.deviceModel || '-'}
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </div>
  );
}
