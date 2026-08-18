import React, { useState, useEffect, ChangeEvent } from 'react';
import axios from 'axios';
import { Shield, Upload, Camera, Activity, AlertTriangle, CheckCircle, Video, Server } from 'lucide-react';

interface Analytics {
  density_percentage: number;
  threat_score: number;
  status: string;
  reasons: string[];
  timestamp: string;
  source_mode: string;
  fps: number;
  resolution: string;
}

function App() {
  const [activeTab, setActiveTab] = useState<'upload' | 'live'>('upload');
  const [uploading, setUploading] = useState<boolean>(false);
  const [videoLoaded, setVideoLoaded] = useState<boolean>(false);
  const [filename, setFilename] = useState<string>('');
  const [telemetry, setTelemetry] = useState<Analytics>({
    density_percentage: 0,
    threat_score: 0,
    status: 'STANDBY',
    reasons: ['Awaiting active video source.'],
    timestamp: new Date().toISOString(),
    source_mode: 'NONE',
    fps: 0,
    resolution: '0x0',
  });

  // Poll analytics telemetry from Python backend every 500ms
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await axios.get<Analytics>('http://127.0.0.1:8000/api/v1/analytics');
        setTelemetry(res.data);
      } catch (err) {
        // Backend offline or reconnecting
      }
    }, 500);
    return () => clearInterval(interval);
  }, []);

  const handleFileUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;

    const file = e.target.files[0];
    setFilename(file.name);
    setUploading(true);

    const formData = new FormData();
    formData.append('file', file);

    try {
      await axios.post('http://127.0.0.1:8000/api/v1/upload-video', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setVideoLoaded(true);
    } catch (err) {
      alert('Failed to upload video to the backend engine.');
    } finally {
      setUploading(false);
    }
  };

  const getThreatColor = (score: number) => {
    if (score < 40) return '#22c55e'; // Green
    if (score < 70) return '#eab308'; // Yellow
    return '#ef4444'; // Red
  };

  return (
    <div style={{ backgroundColor: '#090d16', color: '#e2e8f0', minHeight: '100vh', fontFamily: 'Inter, system-ui, sans-serif' }}>
      
      {/* Top Command Bar */}
      <nav style={{ borderBottom: '1px solid #1e293b', padding: '16px 32px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', backgroundColor: '#0f172a' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Shield color="#38bdf8" size={26} />
          <div>
            <div style={{ fontWeight: 700, fontSize: '18px', letterSpacing: '0.8px', color: '#f8fafc' }}>SENTINEL // Digital Twin Defense</div>
            <div style={{ fontSize: '11px', color: '#64748b' }}>CV OPTICAL THREAT ANALYSIS PIPELINE</div>
          </div>
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{ fontSize: '12px', fontFamily: 'monospace', color: '#94a3b8' }}>
            {new Date(telemetry.timestamp).toLocaleTimeString()} UTC
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', fontWeight: 600, padding: '6px 12px', borderRadius: '20px', backgroundColor: '#1e293b', border: '1px solid #334155' }}>
            <span style={{ height: '8px', width: '8px', borderRadius: '50%', backgroundColor: telemetry.status === 'ANOMALY' ? '#ef4444' : telemetry.status === 'NORMAL' ? '#22c55e' : '#64748b' }}></span>
            {telemetry.status}
          </div>
        </div>
      </nav>

      {/* Main Container */}
      <div style={{ padding: '24px 32px', maxWidth: '1600px', margin: '0 auto', display: 'grid', gridTemplateColumns: '2.2fr 1fr', gap: '24px' }}>
        
        {/* Left Column: Feeds & Gauges */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Stream Selector & Video Display */}
          <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '10px', padding: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              
              {/* Tab Toggle */}
              <div style={{ display: 'flex', gap: '8px', backgroundColor: '#020617', padding: '4px', borderRadius: '6px', border: '1px solid #1e293b' }}>
                <button 
                  onClick={() => setActiveTab('upload')}
                  style={{ padding: '8px 16px', borderRadius: '4px', border: 'none', backgroundColor: activeTab === 'upload' ? '#1e293b' : 'transparent', color: activeTab === 'upload' ? '#38bdf8' : '#64748b', fontSize: '13px', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Video size={16} /> Video File Source
                </button>
                <button 
                  onClick={() => setActiveTab('live')}
                  style={{ padding: '8px 16px', borderRadius: '4px', border: 'none', backgroundColor: activeTab === 'live' ? '#1e293b' : 'transparent', color: activeTab === 'live' ? '#38bdf8' : '#64748b', fontSize: '13px', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Camera size={16} /> Live Optical Camera
                </button>
              </div>

              {activeTab === 'upload' && filename && (
                <span style={{ fontSize: '12px', color: '#38bdf8', fontFamily: 'monospace' }}>{filename}</span>
              )}
            </div>

            {/* Video Box */}
            <div style={{ width: '100%', height: '480px', backgroundColor: '#020617', borderRadius: '8px', border: '1px solid #1e293b', display: 'flex', justifyContent: 'center', alignItems: 'center', overflow: 'hidden', position: 'relative' }}>
              {activeTab === 'upload' ? (
                videoLoaded ? (
                  <img src="http://127.0.0.1:8000/api/v1/video-feed" alt="Uploaded Video Feed" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                ) : (
                  <div style={{ textAlign: 'center', color: '#475569' }}>
                    <Video size={48} style={{ marginBottom: '12px', opacity: 0.4 }} />
                    <p style={{ margin: 0, fontSize: '14px' }}>No media loaded. Ingest an MP4 file using the panel on the right.</p>
                  </div>
                )
              ) : (
                <img src="http://127.0.0.1:8000/api/v1/live-feed" alt="Live Camera Feed" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
              )}
            </div>
          </div>

          {/* Telemetry Gauge Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
            
            {/* Density Gauge */}
            <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600, marginBottom: '8px' }}>CROWD MOTION DENSITY</div>
              <div style={{ fontSize: '28px', fontWeight: 700, color: '#f8fafc', marginBottom: '8px' }}>
                {telemetry.density_percentage.toFixed(1)}%
              </div>
              <div style={{ width: '100%', height: '6px', backgroundColor: '#1e293b', borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{ width: `${Math.min(telemetry.density_percentage, 100)}%`, height: '100%', backgroundColor: '#38bdf8', transition: 'width 0.3s ease' }}></div>
              </div>
            </div>

            {/* Threat Gauge */}
            <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600, marginBottom: '8px' }}>THREAT RISK INDEX</div>
              <div style={{ fontSize: '28px', fontWeight: 700, color: getThreatColor(telemetry.threat_score), marginBottom: '8px' }}>
                {telemetry.threat_score} / 100
              </div>
              <div style={{ width: '100%', height: '6px', backgroundColor: '#1e293b', borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{ width: `${telemetry.threat_score}%`, height: '100%', backgroundColor: getThreatColor(telemetry.threat_score), transition: 'width 0.3s ease' }}></div>
              </div>
            </div>

            {/* Stream Diagnostics */}
            <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '16px' }}>
              <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600, marginBottom: '8px' }}>PIPELINE PERFORMANCE</div>
              <div style={{ fontSize: '13px', color: '#cbd5e1', display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '6px' }}>
                <div><strong>FPS:</strong> {telemetry.fps}</div>
                <div><strong>Resolution:</strong> {telemetry.resolution}</div>
                <div><strong>Source:</strong> {telemetry.source_mode}</div>
              </div>
            </div>

          </div>

        </div>

        {/* Right Column: Ingestion & Diagnostic Logs */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Upload Area */}
          <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '10px', padding: '20px' }}>
            <h3 style={{ margin: '0 0 14px 0', fontSize: '14px', color: '#f8fafc', fontWeight: 600 }}>Media Ingestion</h3>
            <label style={{ 
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', 
              padding: '28px 16px', border: '1px dashed #334155', borderRadius: '8px', cursor: 'pointer',
              backgroundColor: '#1e293b33', transition: 'all 0.2s ease'
            }}>
              <Upload size={22} color="#38bdf8" style={{ marginBottom: '8px' }} />
              <span style={{ fontSize: '13px', color: '#cbd5e1', fontWeight: 500 }}>{uploading ? 'Ingesting File...' : 'Upload Surveillance Video'}</span>
              <span style={{ fontSize: '11px', color: '#64748b', marginTop: '4px' }}>MP4, MOV, AVI, or MKV</span>
              <input type="file" accept="video/*" onChange={handleFileUpload} style={{ display: 'none' }} />
            </label>
          </div>

          {/* Diagnostic Evidence Logs */}
          <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '10px', padding: '20px', flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
              <Activity size={18} color="#38bdf8" />
              <h3 style={{ margin: 0, fontSize: '14px', color: '#f8fafc', fontWeight: 600 }}>Diagnostic Log</h3>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {telemetry.reasons.map((reason, idx) => (
                <div key={idx} style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', padding: '12px', backgroundColor: '#020617', borderRadius: '6px', border: '1px solid #1e293b' }}>
                  {telemetry.status === 'ANOMALY' ? (
                    <AlertTriangle size={16} color="#ef4444" style={{ flexShrink: 0, marginTop: '2px' }} />
                  ) : (
                    <CheckCircle size={16} color="#22c55e" style={{ flexShrink: 0, marginTop: '2px' }} />
                  )}
                  <span style={{ fontSize: '12px', color: '#cbd5e1', lineHeight: '1.4' }}>{reason}</span>
                </div>
              ))}
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}

export default App;