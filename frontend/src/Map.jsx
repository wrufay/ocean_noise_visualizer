import React, { useEffect, useRef } from 'react';
import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import OSM from 'ol/source/OSM';
import { fromLonLat } from 'ol/proj';
import 'ol/ol.css';

function NoiseMap() {
  const mapRef = useRef(null);

  useEffect(() => {
    const map = new Map({
      target: mapRef.current,
      layers: [
        new TileLayer({
          source: new OSM(),  // OpenStreetMap base layer
        }),
      ],
      view: new View({
        center: fromLonLat([-63.5, 44.5]),  // Nova Scotia area
        zoom: 6,
      }),
    });

    return () => map.setTarget(null);  // cleanup on unmount
  }, []);

  return <div ref={mapRef} style={{ width: '100%', height: '100vh' }} />;
}

export default NoiseMap;
