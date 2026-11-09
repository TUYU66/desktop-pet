import { useState, useEffect, useCallback, useRef } from 'react';
import { Table, Button, Modal, Upload, App, Tag, Card, Alert, Select, Progress, Space } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { getFirmwareList, uploadFirmware, deleteFirmware, uploadStm32Firmware, getStm32OtaState, startStm32Ota, getEsp32OtaState, startEsp32Ota } from '../../api/ota';
import dayjs from 'dayjs';
import { createScopedRead } from '../../utils/scopedRead';
import type { ScopedRead } from '../../utils/scopedRead';
import { isNewerVersion, stm32FirmwareAction } from '../../utils/firmwareVersion';
import { esp32UpgradeView } from '../../utils/esp32UpgradeView';
import './ota.css';

export default function OtaList() {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [ota, setOta] = useState<any>(null);
  const [otaError, setOtaError] = useState('');
  const [otaLoading, setOtaLoading] = useState(true);
  const otaRead = useRef<ScopedRead | null>(null);
  const dispatchPending = useRef(false);
  const [esp, setEsp] = useState<any>(null);
  const [espLoading, setEspLoading] = useState(true);
  const [espError, setEspError] = useState('');
  const espRead = useRef<ScopedRead | null>(null);
  const espPending = useRef(false);
  const fetchEsp = useCallback(async () => { await espRead.current?.run(); }, []);
  useEffect(() => {
    const reader = createScopedRead(async signal => {
      const response: any = await getEsp32OtaState(signal);
      if (!response.data || typeof response.data.active !== 'boolean' || typeof response.data.ready !== 'boolean') {
        throw new Error('ESP32 升级状态格式无效');
      }
      return response.data;
    }, {
      started: () => setEspLoading(true),
      succeeded: state => {
        if (espPending.current && !state.active) return;
        setEsp((previous: any) => ({ ...previous, ...state })); setEspError('');
      },
      failed: error => setEspError(error instanceof Error ? error.message : 'ESP32 状态读取失败'),
      settled: () => setEspLoading(false),
    });
    espRead.current = reader;
    void reader.run();
    return () => { reader.stop(); if (espRead.current === reader) espRead.current = null; };
  }, []);
  useEffect(() => {
    if (!esp?.active) return;
    const timer = window.setInterval(() => void fetchEsp(), 2000);
    return () => window.clearInterval(timer);
  }, [esp?.active, fetchEsp]);
  const startEspUpgrade = (row: any) => {
    const connectionId = esp?.connectionId;
    Modal.confirm({
      title: `升级 ESP32 至 ${row.version}？`,
      content: '请先让机器人停稳并保持稳定供电。升级后 ESP32 自动重启，期间语音服务暂不可用。',
      okText: '开始升级', cancelText: '取消',
      onOk: async () => {
        if (espPending.current) return;
        espPending.current = true;
        const jobId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        setEsp((state: any) => ({ ...state, active: true, phase: 'requested', version: row.version, progress: null, progressAvailable: false }));
        try {
          const response: any = await startEsp32Ota(row.id, jobId, connectionId);
          setEsp((state: any) => ({ ...state, ...response.data }));
          setEspError('');
        } catch (error) {
          setEspError(error instanceof Error ? error.message : '升级结果未知，请刷新状态，不要重复点击');
          espPending.current = false;
          await fetchEsp();
        } finally { espPending.current = false; }
      },
    });
  };

  const fetchOta = useCallback(async () => {
    await otaRead.current?.run();
  }, []);
  useEffect(() => {
    const reader = createScopedRead(async signal => {
      const response: any = await getStm32OtaState(signal);
      const state = response?.data;
      if (!state || typeof state.active !== 'boolean' || typeof state.supported !== 'boolean' || typeof state.chassisReady !== 'boolean') {
        throw new Error('底盘升级状态格式无效，请重试');
      }
      return state;
    }, {
      started: () => setOtaLoading(true),
      succeeded: state => {
        if (dispatchPending.current && !state.active) return;
        setOta(state); setOtaError('');
      },
      failed: error => setOtaError(error instanceof Error ? error.message : '升级状态读取失败'),
      settled: () => setOtaLoading(false),
    });
    otaRead.current = reader;
    void reader.run();
    return () => {
      reader.stop();
      if (otaRead.current === reader) otaRead.current = null;
    };
  }, []);
  useEffect(() => {
    if (!ota?.active) return;
    const timer = window.setInterval(() => void fetchOta(), 2000);
    return () => window.clearInterval(timer);
  }, [ota?.active, fetchOta]);
  const startUpgrade = (row: any) => Modal.confirm({
    title: `升级底盘至 ${row.version}？`,
    content: ota?.recovery ? '底盘处于恢复模式，当前无法读取电压。请确认车体已固定、电机停止、供电稳定后继续。' : '请保持稳定供电。设备会退出蓝牙控制并坐下，升级期间无法移动。',
    okText: ota?.recovery ? '已确认，开始恢复' : '开始升级', cancelText: '取消',
    onOk: async () => {
      const jobId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      setOta({ active: true, jobId, phase: 'queued', progress: 0, version: row.version });
      dispatchPending.current = true;
      try {
        const response: any = await startStm32Ota(row.id, jobId, !!ota?.recovery);
        setOta(response.data); setOtaError('');
      } catch (error) {
        setOtaError(error instanceof Error ? error.message : '升级请求结果未知，请刷新进度');
        message.error(error instanceof Error ? error.message : '升级请求结果未知，请刷新进度');
        dispatchPending.current = false;
        await fetchOta();
      } finally {
        dispatchPending.current = false;
      }
    },
  });
  const phases: Record<string, string> = {
    idle: '尚未开始升级', queued: '正在准备升级请求', downloading: '正在下载并校验固件',
    preparing: '正在退出控制并确认坐下', bootloader: '正在进入升级模式', transferring: '正在传输固件',
    installing: '正在安装固件', recovering: '正在尝试恢复之前的安装', verifying: '正在确认设备启动', completed: '升级完成，设备已启动', failed: '升级未完成', unconfirmed: '上次升级结果未确认',
  };
  const { message } = App.useApp();
  const espView = esp32UpgradeView(esp);

  const fetchData = useCallback(async () => {
    const token = ++generation.current;
    setLoading(true);
    try {
      const res: any = await getFirmwareList(page, size);
      if (token !== generation.current) return;
      const count = Number(res.data?.total || 0);
      setTotal(count);
      if (page > 1 && (page - 1) * size >= count) setPage(Math.max(1, Math.ceil(count / size)));
      setData(res.data?.records || []);
      setError('');
    } catch (e) {
      if (token === generation.current) setError(e instanceof Error ? e.message : '固件列表读取失败');
    } finally {
      if (token === generation.current) setLoading(false);
    }
  }, [page, size]);

  useEffect(() => { void fetchData(); return () => { ++generation.current; }; }, [fetchData]);

  const handleUpload = async (values: { boardType: string; file: File }) => {
    try {
      const response: any = values.boardType === 'stm32-YB-EST01-V2.0'
        ? await uploadStm32Firmware(values.file)
        : await uploadFirmware('', values.boardType, 0, values.file);
      message.success(`上传成功，版本 ${response.data?.version || '已识别'}`);
      setUploadOpen(false);
      fetchData();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '上传失败');
      throw error;
    }
  };

  const handleDelete = (id: number) => {
    Modal.confirm({
      title: '确认删除',
      onOk: async () => {
        try {
          await deleteFirmware(id);
          message.success('已删除');
          fetchData();
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  const columns: ColumnsType<any> = [
    { title: '版本', dataIndex: 'version', key: 'version', width: 100 },
    { title: '固件类型', dataIndex: 'boardType', key: 'boardType', width: 180,
      render: (value: string) => value === 'stm32-YB-EST01-V2.0' ? <Tag color="blue">STM32 底盘</Tag> : <span>ESP32 · {value}</span> },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: number) => (
        <Tag color={s === 1 ? 'green' : 'default'}>{s === 1 ? '启用' : '禁用'}</Tag>
      ),
    },
    {
      title: '文件大小',
      dataIndex: 'fileSize',
      key: 'fileSize',
      width: 100,
      render: (s: number) => (s ? `${(s / 1024 / 1024).toFixed(1)} MB` : '-'),
    },
    {
      title: '创建时间',
      dataIndex: 'createDate',
      key: 'createDate',
      width: 160,
      render: (t: string) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-'),
    },
    {
      title: '下载次数',
      dataIndex: 'downloadCount',
      key: 'downloadCount',
      width: 80,
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_, r) => (
        <Space>
        {r.boardType === 'stm32-YB-EST01-V2.0' && <Button type="link" disabled={!stm32FirmwareAction(r.version, ota).allowed || r.status !== 1 || esp?.active || otaLoading || ota?.active || !ota?.supported || !ota?.chassisReady || !!otaError} onClick={() => startUpgrade(r)}>{stm32FirmwareAction(r.version, ota).label}</Button>}
        {r.boardType === 'bread-compact-wifi-lcd' && <Button type="link"
          disabled={espLoading || !!espError || !esp?.ready || esp?.active || ota?.active || esp?.chassisActive || !isNewerVersion(r.version, esp?.currentVersion) || r.status !== 1}
          onClick={() => startEspUpgrade(r)}>升级 ESP32</Button>}
        <Button type="link" danger disabled={ota?.active || esp?.active} onClick={() => handleDelete(r.id)}>
          删除
        </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="utility-page page-width"><div className="page-heading"><div><h1>固件管理</h1><p>开机自动检查更新，也可手动升级。</p></div></div>
      {error && <Alert type="error" showIcon title={error} action={<Button onClick={() => void fetchData()}>重试</Button>} />}
      <Card title="ESP32 升级" style={{ marginBottom: 20 }} extra={<Button loading={espLoading} onClick={() => void fetchEsp()}>刷新状态</Button>}>
        {espLoading && !esp && <Alert type="info" showIcon title="正在获取 ESP32 状态…" />}
        {espError && <Alert type="warning" showIcon title={espError} />}
        {esp && <Space orientation="vertical" style={{ width: '100%' }}>
          <span>当前版本：{esp.currentVersion || '尚未读取'}{!esp.ready ? ' · 等待设备连接或应答' : ''}</span>
          {esp.phase !== 'idle' && <Alert showIcon type={esp.phase === 'completed' ? 'success' : esp.phase === 'failed' ? 'error' : 'info'}
            title={espView.label} />}
          {esp.phase !== 'idle' && (espView.percent === null && esp.active
            ? <div className="ota-progress-pending" role="progressbar" aria-label="正在等待 ESP32 上报升级进度" aria-busy="true"><span /></div>
            : <Progress percent={espView.percent ?? 0} status={espView.status} format={value => espView.percent === null ? '待确认' : `${value}%`} />)}
        </Space>}
      </Card>
      <Card title="底盘升级" style={{ marginBottom: 20 }} extra={<Button loading={otaLoading} onClick={() => void fetchOta()}>刷新进度</Button>}>
        {otaLoading && !ota && <Alert type="info" showIcon title="正在获取底盘状态…" />}
        {otaError && <Alert type="warning" showIcon title={otaError} description="无法确认当前升级状态，请刷新进度，不要重复发起升级。" />}
        {ota?.recovery && !ota.active && <Alert type="warning" showIcon title={ota.recoveryState === 0 ? '启动日志无效，请使用 ST-Link 整包恢复。' : '底盘处于升级恢复模式，可选择固件重新升级；请先固定车体并确认供电稳定。'} />}
        {ota && !ota.active && !ota.chassisReady && <Alert type="info" title="底盘尚未就绪。首次使用请烧录支持网页升级的固件，连接后等待约 10 秒并刷新进度。" />}
        {ota && <><p>当前底盘版本：{ota.installedVersion || '尚未报告（可手动升级）'}</p><p>{phases[ota.phase] || '升级状态未知'}{ota.version ? ` · ${ota.version}` : ''}</p>
          {ota.phase !== 'idle' && <Progress percent={ota.progress || 0} status={ota.phase === 'failed' ? 'exception' : ota.active ? 'active' : ota.phase === 'completed' ? 'success' : 'normal'} />}
          {ota.error && <Alert showIcon type="error" title={ota.error} />}</>}
      </Card>
      <Card
        style={{
          borderRadius: 16,
          border: 'none',
          boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 16,
          }}
        >
          <span style={{ fontSize: 15, fontWeight: 600 }}>固件列表</span>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            onClick={() => setUploadOpen(true)}
          >
            上传固件
          </Button>
        </div>
        <Table scroll={{ x: 700 }}
          rowKey="id"
          columns={columns}
          dataSource={data}
          loading={loading}
          pagination={{ current: page, pageSize: size, total, showSizeChanger: true,
            onChange: (next, nextSize) => { setPage(nextSize === size ? next : 1); setSize(nextSize); } }}
          style={{ borderRadius: 12 }}
        />
      </Card>

      <Modal
        title="上传固件"
        open={uploadOpen}
        onCancel={() => setUploadOpen(false)}
        footer={null}
        width={460}
      >
        <UploadForm onFinish={handleUpload} />
      </Modal>
    </div>
  );
}

function UploadForm({
  onFinish,
}: {
  onFinish: (v: any) => Promise<void>;
}) {
  const [boardType, setBoardType] = useState('stm32-YB-EST01-V2.0');
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const { message } = App.useApp();

  const submit = async () => {
    if (!file) {
      message.warning('请选择固件文件');
      return;
    }
    setLoading(true);
    try {
      await onFinish({ boardType, file });
    } catch { /* Parent displays the upload error and keeps the form open. */ } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Select value={boardType} onChange={value => {setBoardType(value);setFile(null);}} options={[
        {value:'stm32-YB-EST01-V2.0',label:'STM32 底盘（ZIP 固件包）'},
        {value:'bread-compact-wifi-lcd',label:'ESP32（BIN 固件）'},
      ]} />
      {boardType === 'stm32-YB-EST01-V2.0' ? <Alert type="info" title="请选择项目打包工具生成的 ZIP，版本从固件包读取。不要上传 HEX 或 Bootloader。" /> : <Alert type="info" title="请选择发布命令生成的应用 BIN，版本自动从固件读取，无需填写。" />}
      <Upload
        accept={boardType === 'stm32-YB-EST01-V2.0' ? '.zip' : '.bin'}
        fileList={file ? [{ uid: 'firmware', name: file.name, status: 'done' }] : []}
        onRemove={() => setFile(null)}
        beforeUpload={(f) => {
          setFile(f);
          return false;
        }}
        maxCount={1}
      >
        <Button icon={<UploadOutlined />}>选择固件文件</Button>
      </Upload>
      {file && <span style={{ color: '#6b7280', fontSize: 13 }}>{file.name}</span>}
      <Button type="primary" onClick={submit} loading={loading}>
        上传
      </Button>
    </div>
  );
}
