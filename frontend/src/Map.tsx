import { useEffect, useRef, useState } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import OSM from 'ol/source/OSM';
import { fromLonLat, transformExtent } from 'ol/proj';
import Feature, { type FeatureLike } from 'ol/Feature';
import LineString from 'ol/geom/LineString';
import Point from 'ol/geom/Point';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { Style, Stroke, Circle as CircleStyle, Fill } from 'ol/style';
import Draw, { createBox } from 'ol/interaction/Draw';
import 'ol/ol.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface Vessel {
  mmsi: number;
  vessel_name: string | null;
  ship_type: string | number | null;
  source: string;
}

interface RoutePoint {
  time: number | string;
  latitude: number;
  longitude: number;
  sog: number | null;
  cog: number | null;
  source: string;
}

function formatTime(t: number | string): string {
  const s = String(t);
  // unix epoch (CCG)
  if (/^\d+$/.test(s)) {
    const d = new Date(Number(s) * 1000);
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  }
  // compact ISO e.g. 20251201T035835Z
  if (s.length >= 15)
    return `${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)} ${s.slice(9,11)}:${s.slice(11,13)}:${s.slice(13,15)} UTC`;
  return s;
}

function featureStyle(feature: FeatureLike): Style {
  const type = feature.getGeometry()?.getType();
  if (type === 'LineString') {
    return new Style({
      stroke: new Stroke({ color: '#127475', width: 2 }),
    });
  }
  const sog = (feature.get('sog') as number) || 0;
  const color = sog > 10 ? '#e63946' : sog > 3 ? '#f4a261' : '#2a9d8f';
  return new Style({
    image: new CircleStyle({
      radius: 4,
      fill: new Fill({ color }),
      stroke: new Stroke({ color: '#fff', width: 1 }),
    }),
  });
}

function ShipMap() {
  const mapRef    = useRef<HTMLDivElement>(null);
  const mapObj    = useRef<Map | null>(null);
  const sourceRef = useRef(new VectorSource());
  const drawRef   = useRef<Draw | null>(null);

  interface Popup {
    x: number; y: number;
    time: number | string; lat: number; lon: number;
    sog: number | null; cog: number | null; source: string;
  }

  const [vessels, setVessels]       = useState<Vessel[]>([]);
  const [search, setSearch]         = useState('');
  const [selected, setSelected]     = useState<Vessel | null>(null);
  const [start, setStart]           = useState('2025-03-11');
  const [end, setEnd]               = useState('2025-03-13');
  const [loading, setLoading]       = useState(false);
  const [pointCount, setPointCount] = useState<number | null>(null);
  const [drawMode, setDrawMode]     = useState(false);
  const [popup, setPopup]           = useState<Popup | null>(null);

  useEffect(() => {
    if (!mapRef.current) return;
    const map = new Map({
      target: mapRef.current,
      layers: [
        new TileLayer({ source: new OSM() }),
        new VectorLayer({
          source: sourceRef.current,
          style: featureStyle,
        }),
      ],
      view: new View({
        center: fromLonLat([-63.5, 44.5]),
        zoom: 6,
      }),
    });
    map.on('click', e => {
      map.forEachFeatureAtPixel(e.pixel, feature => {
        if (feature.getGeometry()?.getType() !== 'Point') return;
        setPopup({
          x: e.pixel[0] + 288,
          y: e.pixel[1],
          time:   feature.get('time'),
          lat:    feature.get('lat'),
          lon:    feature.get('lon'),
          sog:    feature.get('sog'),
          cog:    feature.get('cog'),
          source: feature.get('source'),
        });
        return true;
      }) ?? setPopup(null);
    });

    mapObj.current = map;
    return () => map.setTarget(undefined);
  }, []);

  useEffect(() => {
    fetch(`${API}/api/vessels`)
      .then(r => r.json())
      .then(d => setVessels(d.vessels || []))
      .catch(console.error);
  }, []);

  useEffect(() => {
    const map = mapObj.current;
    if (!map) return;

    if (drawRef.current) {
      map.removeInteraction(drawRef.current);
      drawRef.current = null;
    }

    if (drawMode) {
      const draw = new Draw({
        source: new VectorSource(),
        type: 'Circle',
        geometryFunction: createBox(),
      });

      draw.on('drawend', e => {
        const extent = e.feature.getGeometry()!.getExtent();
        const [min_lon, min_lat, max_lon, max_lat] = transformExtent(extent, 'EPSG:3857', 'EPSG:4326');
        setDrawMode(false);

        const params = new URLSearchParams({
          min_lat: String(min_lat),
          max_lat: String(max_lat),
          min_lon: String(min_lon),
          max_lon: String(max_lon),
        });
        fetch(`${API}/api/vessels/area?${params}`)
          .then(r => r.json())
          .then(d => setVessels(d.vessels || []))
          .catch(console.error);
      });

      map.addInteraction(draw);
      drawRef.current = draw;
    }
  }, [drawMode]);

  function loadRoute() {
    if (!selected) return;
    setLoading(true);
    setPointCount(null);
    sourceRef.current.clear();

    const params = new URLSearchParams({
      start: `${start}T00:00:00`,
      end:   `${end}T23:59:59`,
    });

    fetch(`${API}/api/vessel/${selected.mmsi}/route?${params}`)
      .then(r => r.json())
      .then((data: { points: RoutePoint[] }) => {
        const pts = data.points || [];
        setPointCount(pts.length);
        if (pts.length === 0) return;

        const coords = pts.map(p => fromLonLat([p.longitude, p.latitude]));
        sourceRef.current.addFeature(new Feature({ geometry: new LineString(coords) }));

        pts.forEach(p => {
          const f = new Feature({
            geometry: new Point(fromLonLat([p.longitude, p.latitude])),
            sog: p.sog,
            cog: p.cog,
            time: p.time,
            lat: p.latitude,
            lon: p.longitude,
            source: p.source,
          });
          sourceRef.current.addFeature(f);
        });

        const extent = sourceRef.current.getExtent();
        if (extent) mapObj.current!.getView().fit(extent, { padding: [60, 60, 60, 60], maxZoom: 12 });
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }

  function resetVessels() {
    sourceRef.current.clear();
    setSelected(null);
    setPointCount(null);
    fetch(`${API}/api/vessels`)
      .then(r => r.json())
      .then(d => setVessels(d.vessels || []))
      .catch(console.error);
  }

  const filtered = vessels.filter(v => {
    const q = search.toLowerCase();
    return (
      String(v.mmsi).includes(q) ||
      (v.vessel_name || '').toLowerCase().includes(q) ||
      String(v.ship_type || '').toLowerCase().includes(q)
    );
  });

  return (
    <div className="relative w-full h-screen">

      <div className="absolute top-0 left-0 h-full w-72 bg-white shadow-lg z-10 flex flex-col overflow-hidden">
        <div className="p-4 border-b">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="font-semibold text-[#127475] text-lg">Vessel Tracker</h2>
            <span className="text-xs text-gray-400">
              {filtered.length !== vessels.length
                ? `${filtered.length} / ${vessels.length}`
                : `${vessels.length} vessels`}
            </span>
          </div>

          <input
            className="w-full border rounded px-2 py-1 text-sm mb-3"
            placeholder="Search vessel name or MMSI..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />

          <div className="flex gap-2 mb-3">
            <button
              onClick={() => setDrawMode(m => !m)}
              className={`flex-1 rounded py-1.5 text-sm border ${drawMode ? 'bg-[#127475] text-white' : 'text-[#127475] border-[#127475]'}`}
            >
              {drawMode ? 'Drawing...' : 'Filter by Area'}
            </button>
            <button
              onClick={resetVessels}
              className="flex-1 rounded py-1.5 text-sm border text-gray-500 border-gray-300"
            >
              Reset
            </button>
          </div>

          <div className="flex flex-col gap-2 text-sm">
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-500 text-xs">Start date</span>
              <input type="date" className="border rounded px-2 py-1"
                value={start} onChange={e => setStart(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-gray-500 text-xs">End date</span>
              <input type="date" className="border rounded px-2 py-1"
                value={end} onChange={e => setEnd(e.target.value)} />
            </label>
          </div>

          <button
            onClick={loadRoute}
            disabled={!selected || loading}
            className="mt-3 w-full bg-[#127475] text-white rounded py-1.5 text-sm disabled:opacity-40"
          >
            {loading ? 'Loading...' : 'Show Route'}
          </button>

          {pointCount !== null && (
            <p className="text-xs text-gray-400 mt-1 text-center">
              {pointCount === 0 ? 'No data for this period.' : `${pointCount} position points`}
            </p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 && (
            <p className="text-xs text-gray-400 p-4 text-center">
              {vessels.length === 0 ? 'Loading vessels...' : 'No vessels match.'}
            </p>
          )}
          {filtered.map(v => (
            <button
              key={v.mmsi}
              onClick={() => { setSelected(v); sourceRef.current.clear(); setPointCount(null); }}
              className={`w-full text-left px-4 py-2 border-b text-sm hover:bg-gray-50 ${
                selected?.mmsi === v.mmsi ? 'bg-teal-50 border-l-4 border-l-[#127475]' : ''
              }`}
            >
              <div className="font-medium truncate">{v.vessel_name || 'Unknown'}</div>
              <div className="text-xs text-gray-400">{v.mmsi} · {v.ship_type || '—'} · {v.source}</div>
            </button>
          ))}
        </div>
      </div>

      <div ref={mapRef} className={`w-full h-full pl-72 ${drawMode ? 'cursor-crosshair' : ''}`} />

      <div className="absolute bottom-4 right-4 bg-white rounded shadow px-3 py-2 text-xs z-10">
        <div className="font-medium mb-1 text-gray-600">Speed (knots)</div>
        <div className="flex items-center gap-1.5 mb-0.5"><span className="w-3 h-3 rounded-full bg-[#2a9d8f] inline-block"/>&lt; 3</div>
        <div className="flex items-center gap-1.5 mb-0.5"><span className="w-3 h-3 rounded-full bg-[#f4a261] inline-block"/>3 – 10</div>
        <div className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-full bg-[#e63946] inline-block"/>&gt; 10</div>
      </div>

      {drawMode && (
        <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-[#127475] text-white text-sm px-4 py-2 rounded shadow z-20">
          Draw a box on the map to filter vessels
        </div>
      )}

      {popup && (
        <div
          className="absolute z-30 bg-white border border-gray-200 rounded shadow-lg px-3 py-2 text-xs pointer-events-none"
          style={{ left: popup.x + 8, top: popup.y - 8 }}
        >
          <div className="font-semibold text-[#127475] mb-1">{popup.source}</div>
          <div className="text-gray-600 space-y-0.5">
            <div><span className="text-gray-400">Time </span>{formatTime(popup.time)}</div>
            <div><span className="text-gray-400">Lat  </span>{popup.lat?.toFixed(4)}</div>
            <div><span className="text-gray-400">Lon  </span>{popup.lon?.toFixed(4)}</div>
            <div><span className="text-gray-400">SOG  </span>{popup.sog != null ? `${popup.sog} kt` : '—'}</div>
            <div><span className="text-gray-400">COG  </span>{popup.cog != null ? `${popup.cog}°` : '—'}</div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ShipMap;
