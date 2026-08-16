/**
 * Renders an STL string to a tiny PNG data URL — the low-quality per-object
 * preview shown in ScadObjectList's rows. A fully offscreen three.js scene
 * (no OrbitControls, no animation loop, canvas never attached to the DOM),
 * built fresh and torn down for each thumbnail: at this size and frequency
 * that's cheaper than keeping N live scenes around, and it means a stale
 * thumbnail can never keep rendering after its row is gone.
 */
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

const THUMBNAIL_SIZE = 40; // px — matches the row height in ScadObjectList

export function renderStlThumbnail(stl: string): string {
  const geometry = new STLLoader().parse(stl);
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  const box = geometry.boundingBox ?? new THREE.Box3(new THREE.Vector3(), new THREE.Vector3(1, 1, 1));
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());

  const canvas = document.createElement("canvas");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE, false);

  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(1, -1, 1.5);
  scene.add(key);

  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({ color: 0x9ca3af, roughness: 0.6, metalness: 0.05, side: THREE.DoubleSide }),
  );
  scene.add(mesh);

  // Same framing math as StlPreview's frameCamera, simplified for a fixed
  // square viewport with no controls to hand the result off to.
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
  camera.up.set(0, 0, 1); // Z-up, matching OpenSCAD
  const radius = Math.max(size.length() / 2, 0.01);
  const fov = (camera.fov * Math.PI) / 180;
  const distance = (radius / Math.sin(fov / 2)) * 1.25;
  const direction = new THREE.Vector3(0.6, -1, 0.55).normalize();
  camera.position.copy(center.clone().addScaledVector(direction, distance));
  camera.lookAt(center);
  camera.near = Math.max(distance / 100, 0.01);
  camera.far = distance * 100;
  camera.updateProjectionMatrix();

  renderer.render(scene, camera);
  const dataUrl = canvas.toDataURL("image/png");

  geometry.dispose();
  mesh.material.dispose();
  renderer.dispose();

  return dataUrl;
}
