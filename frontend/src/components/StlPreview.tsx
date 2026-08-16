import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Switch } from "./ui";

// A generic FDM printer's bed footprint, in the same millimeter units
// OpenSCAD models use — big enough to place a typical part on, small enough
// to still read as "the bed" rather than an infinite floor.
const BED_SIZE = 220;

// Axis arrows: long enough to read as X/Y/Z against a part sized like the
// bed, thick enough to see, short enough not to swamp small geometry.
const AXIS_LENGTH = 30;
const AXIS_HEAD_LENGTH = 6;
const AXIS_SHAFT_RADIUS = 0.6;
const AXIS_HEAD_RADIUS = 2.2;

const BACKGROUND = 0x0d0f14; // ink-900, matches the panel it sits in

// Shared with the legend overlay (see StlPreview's JSX) so the arrows and
// their labels always agree.
const AXIS_COLORS = { x: "#ef4444", y: "#22c55e", z: "#3b82f6" } as const;

function buildAxisArrow(color: THREE.ColorRepresentation, direction: THREE.Vector3): THREE.Object3D {
  const group = new THREE.Group();
  const material = new THREE.MeshBasicMaterial({ color });
  const shaftLength = AXIS_LENGTH - AXIS_HEAD_LENGTH;

  const shaft = new THREE.Mesh(
    new THREE.CylinderGeometry(AXIS_SHAFT_RADIUS, AXIS_SHAFT_RADIUS, shaftLength, 12),
    material,
  );
  shaft.position.y = shaftLength / 2;
  group.add(shaft);

  const head = new THREE.Mesh(new THREE.ConeGeometry(AXIS_HEAD_RADIUS, AXIS_HEAD_LENGTH, 16), material);
  head.position.y = shaftLength + AXIS_HEAD_LENGTH / 2;
  group.add(head);

  // Both primitives are built pointing along +Y — rotate the whole arrow
  // onto the requested axis instead of re-deriving each geometry per axis.
  group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
  return group;
}

/** Red/green/blue X/Y/Z arrows, planted at the bed's front-left corner
 * (-110,-110,0 for the default 220mm bed) rather than its center — the bed
 * moved there to center itself on the origin (see buildBedPlane), and the
 * arrows move with it so they still mark the bed's actual corner. */
function buildAxes(): THREE.Object3D {
  const axes = new THREE.Group();
  axes.add(buildAxisArrow(AXIS_COLORS.x, new THREE.Vector3(1, 0, 0))); // X — red
  axes.add(buildAxisArrow(AXIS_COLORS.y, new THREE.Vector3(0, 1, 0))); // Y — green
  axes.add(buildAxisArrow(AXIS_COLORS.z, new THREE.Vector3(0, 0, 1))); // Z — blue
  axes.position.set(-BED_SIZE / 2, -BED_SIZE / 2, 0);
  return axes;
}

/** A transparent print-bed plane with grid lines, sized `BED_SIZE` mm² and
 * sitting in the XY plane at z=0, centered on the origin (-110..110 on each
 * axis for the default 220mm bed) so a part built around (0,0,0) — the usual
 * OpenSCAD `center=true` convention — sits in the middle of the bed. */
function buildBedPlane(): THREE.Object3D {
  const group = new THREE.Group();

  // PlaneGeometry is already centered at its local origin, so no extra
  // translation is needed to land it on -110..110.
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(BED_SIZE, BED_SIZE),
    new THREE.MeshBasicMaterial({
      color: 0x64748b,
      transparent: true,
      opacity: 0.06,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
  );
  group.add(plane);

  // GridHelper is drawn in the XZ plane (Y-up) by default, centered at its
  // local origin; rotate it flat onto XY (Z-up) to match OpenSCAD/three's
  // coordinate axes used here — no translation needed, same reason as above.
  const grid = new THREE.GridHelper(BED_SIZE, BED_SIZE / 10, 0x94a3b8, 0x3a4356);
  grid.rotateX(Math.PI / 2);
  const gridMaterial = grid.material as THREE.Material;
  gridMaterial.transparent = true;
  gridMaterial.opacity = 0.4;
  gridMaterial.depthWrite = false;
  group.add(grid);

  return group;
}

/** The bed's own footprint as a Box3, -110..110 on x/y, 0..`height` on z —
 * used to keep the camera framed on the bed even before/without a model. */
function bedBox(height: number): THREE.Box3 {
  const half = BED_SIZE / 2;
  return new THREE.Box3(new THREE.Vector3(-half, -half, 0), new THREE.Vector3(half, half, height));
}

function frameCamera(camera: THREE.PerspectiveCamera, controls: OrbitControls, box: THREE.Box3) {
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 1);
  const fov = (camera.fov * Math.PI) / 180;
  const distance = (radius / Math.sin(fov / 2)) * 1.1;

  const direction = new THREE.Vector3(0.6, -1, 0.55).normalize();
  camera.position.copy(center.clone().addScaledVector(direction, distance));
  camera.near = Math.max(distance / 100, 0.1);
  camera.far = distance * 100;
  camera.updateProjectionMatrix();

  controls.target.copy(center);
  controls.update();
}

/**
 * Plain three.js STL preview (no react-three-fiber) — loads the mesh at
 * `url` into a scene that also carries a print-bed-sized reference grid and
 * X/Y/Z arrows at its front-left corner (plus a matching color-coded legend
 * in the bottom-left corner of the view), so a part's placement relative to
 * the bed is visible at a glance. Deliberately doesn't recenter the loaded
 * geometry: it renders at the same coordinates OpenSCAD placed it at, which
 * is the whole point of showing the bed and its corner alongside it.
 */
export function StlPreview({ url, className = "" }: { url: string; className?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const axesRef = useRef<THREE.Object3D | null>(null);
  const bedRef = useRef<THREE.Object3D | null>(null);

  // What the settings popup lets the user hide — bed, axes and their legend
  // together, since the legend is meaningless without the arrows it labels.
  // On by default, toggled without tearing down and rebuilding the three.js
  // scene (see the effect below).
  const [showExtras, setShowExtras] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!settingsOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) setSettingsOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [settingsOpen]);

  // Toggle visibility on the live objects rather than re-running the scene
  // effect — flipping the switch shouldn't reload the STL or reset the camera.
  useEffect(() => {
    if (axesRef.current) axesRef.current.visible = showExtras;
    if (bedRef.current) bedRef.current.visible = showExtras;
  }, [showExtras]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BACKGROUND);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
    camera.up.set(0, 0, 1); // Z-up, matching OpenSCAD

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(BED_SIZE, -BED_SIZE, BED_SIZE * 1.5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.35);
    fill.position.set(-BED_SIZE, BED_SIZE * 0.5, BED_SIZE);
    scene.add(fill);

    const bed = buildBedPlane();
    bed.visible = showExtras;
    scene.add(bed);
    bedRef.current = bed;

    const axes = buildAxes();
    axes.visible = showExtras;
    scene.add(axes);
    axesRef.current = axes;

    let mesh: THREE.Mesh | null = null;
    let disposed = false;

    new STLLoader().load(
      url,
      (geometry) => {
        if (disposed) return;
        geometry.computeVertexNormals();
        mesh = new THREE.Mesh(
          geometry,
          new THREE.MeshStandardMaterial({ color: 0x9ca3af, roughness: 0.6, metalness: 0.05, side: THREE.DoubleSide }),
        );
        scene.add(mesh);

        geometry.computeBoundingBox();
        const box = (geometry.boundingBox ?? new THREE.Box3()).clone();
        box.union(bedBox(0));
        frameCamera(camera, controls, box);
      },
      undefined,
      (err) => console.error("[StlPreview] failed to load STL", err),
    );

    // Until the model loads, still frame on the bed so axes/grid are visible.
    frameCamera(camera, controls, bedBox(BED_SIZE * 0.3));

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = container;
      if (w === 0 || h === 0) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    let frame: number;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      container.removeChild(renderer.domElement);
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose());
          else obj.material.dispose();
        }
      });
      axesRef.current = null;
      bedRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- showExtras
    // intentionally excluded: applied once here at scene build, then kept in
    // sync by the effect above without rebuilding the whole scene (and
    // reloading the STL, and resetting the camera) on every toggle.
  }, [url]);

  return (
    <div className={`relative ${className}`}>
      <div ref={containerRef} className="absolute inset-0" />

      {showExtras && (
        // Axis legend — colors match buildAxes' arrows via AXIS_COLORS.
        <div className="absolute left-2 bottom-2 flex gap-2 text-xs font-mono font-semibold pointer-events-none select-none">
          <span style={{ color: AXIS_COLORS.x }}>X</span>
          <span style={{ color: AXIS_COLORS.y }}>Y</span>
          <span style={{ color: AXIS_COLORS.z }}>Z</span>
        </div>
      )}

      <div ref={settingsRef} className="absolute top-2 right-2">
        <button
          type="button"
          className="btn btn-ghost px-1.5 py-1.5 bg-ink-800/80 backdrop-blur"
          title="Viewer settings"
          aria-label="Viewer settings"
          onClick={() => setSettingsOpen((o) => !o)}
        >
          <span aria-hidden>⚙</span>
        </button>
        {settingsOpen && (
          <div className="absolute right-0 top-full mt-1 w-48 rounded-lg border border-ink-600 bg-ink-800 p-2 text-xs shadow-lg z-10">
            <div className="flex items-center justify-between gap-3 px-1 py-1">
              <span>Bed &amp; axes</span>
              <Switch checked={showExtras} onChange={setShowExtras} label="Show bed and axes" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
