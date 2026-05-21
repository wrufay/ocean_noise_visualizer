import React, { useEffect, useRef } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import OSM from 'ol/source/OSM';
import { fromLonLat } from 'ol/proj';
import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import { Circle as CircleStyle, Fill, Style } from 'ol/style';
import 'ol/ol.css';

function NoiseMap() {
  const mapRef = useRef(null);

  useEffect(() => {
    // Create a vector source to hold our noise markers
    const vectorSource = new VectorSource();

    const map = new Map({
      target: mapRef.current,
      layers: [
        new TileLayer({ source: new OSM() }),
        new VectorLayer({
          source: vectorSource,
          style: new Style({
            image: new CircleStyle({
              radius: 7,
              fill: new Fill({ color: '#f2542d' }),
            }),
          }),
        }),
      ],
      view: new View({
        center: fromLonLat([-63.5, 44.5]),
        zoom: 6,
      }),
    });

    // Fetch noise data from backend and add marker
    fetch('https://ocean-viz.up.railway.app/api/noise')
      .then(res => res.json())
      .then(data => {
        const { lat, lon } = data.location;
        const marker = new Feature({
          geometry: new Point(fromLonLat([lon, lat])),
        });
        vectorSource.addFeature(marker);
      });

    return () => map.setTarget(null);
  }, []);

  return <div ref={mapRef} style={{ width: '100%', height: '100vh' }} />;
}

export default NoiseMap;
