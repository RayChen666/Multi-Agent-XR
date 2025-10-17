/* eslint-disable sort-imports */
import * as THREE from 'three';

import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { Text } from 'troika-three-text';
import { XR_BUTTONS } from 'gamepad-wrapper';
import { gsap } from 'gsap';
import { init } from './init.js';

function setupScene({ scene, camera, renderer, player, controllers }) {
    const floorGeometry = new THREE.PlaneGeometry(6, 6);
    const floorMaterial = new THREE.MeshStandardMaterial({ color: 'blue' });

    const floor = new THREE.Mesh(floorGeometry, floorMaterial);

    floor.rotateX(-Math.PI / 2);
    scene.add(floor);

    const gltfLoader = new GLTFLoader();

    gltfLoader.load('assets/gltf-glb-models/table/Table.gltf', (gltf) => {
		scene.add(gltf.scene);
	});
}

function onFrame(delta, time, { scene, camera, renderer, player, controllers },) {

}


init(setupScene, onFrame);