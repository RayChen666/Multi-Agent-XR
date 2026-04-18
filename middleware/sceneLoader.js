/**
 * Scene database loader - load scene from JSON
 */

import sceneDataImport from './sceneData.json';

let sceneDatabase = null;

export async function loadSceneDatabase() {
    try {
        // Use the imported JSON directly
        sceneDatabase = sceneDataImport;
        console.log(`Scene database loaded: ${sceneDatabase.metadata.sceneName}`);
        return sceneDatabase;
    } catch (error) {
        console.error('Failed to load scene database:', error);
        throw error;
    }
}

// Get the current scene database
export function getSceneDatabase() {
    if (!sceneDatabase) {
        throw new Error('Scene database not loaded. Call loadSceneDatabase() first.');
    }
    return sceneDatabase;
}

// Helper functions for agents to query the scene
export class SceneQuery {
    constructor(database = null) {
        this.db = database || getSceneDatabase();
    }

    // Get object by ID
    getObjectById(id) {
        return this.db.objects.find(obj => obj.id === id);
    }

    // Get objects by name
    getObjectsByName(name) {
        return this.db.objects.filter(obj => 
            obj.name.toLowerCase().includes(name.toLowerCase())
        );
    }

    // Get objects by category
    getObjectsByCategory(category) {
        return this.db.objects.filter(obj => obj.category === category);
    }

    // Find objects near a position
    getObjectsNearPosition(position, radius = 1.0) {
        return this.db.objects.filter(obj => {
            const dx = obj.position.x - position.x;
            const dy = obj.position.y - position.y;
            const dz = obj.position.z - position.z;
            const distance = Math.sqrt(dx*dx + dy*dy + dz*dz);
            return distance <= radius;
        });
    }

    // Get spatial relationships
    getSpatialRelations(objectId) {
        const obj = this.getObjectById(objectId);
        return obj ? obj.spatialRelations : null;
    }

    // Get all movable objects
    getMovableObjects() {
        return this.db.objects.filter(obj => obj.properties.movable);
    }

    // Update object position (for agents to modify)
    updateObjectPosition(id, newPosition) {
        const obj = this.getObjectById(id);
        if (obj) {
            obj.position = { ...obj.position, ...newPosition };
            return true;
        }
        return false;
    }

    // Update object rotation
    updateObjectRotation(id, newRotation) {
        const obj = this.getObjectById(id);
        if (obj) {
            obj.rotation = {...obj.rotation, ...newRotation };
            return true;
        }
        return false;
    }

    // Add new object (for asset agent)
    addObject(objectData) {
        this.db.objects.push(objectData);
        return objectData.id;
    }

    // Remove object
    removeObject(id) {
        const index = this.db.objects.findIndex(obj => obj.id === id);
        if (index !== -1) {
            this.db.objects.splice(index, 1);
            return true;
        }
        return false;
    }

    // Get scene bounds for spatial validation
    getSceneBounds() {
        return this.db.metadata.bounds;
    }

    // Save current state (optional - for persistence)
    async saveToServer(endpoint = '/api/scene/save') {
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(this.db)
            });
            return response.ok;
        } catch (error) {
            console.error('Failed to save scene:', error);
            return false;
        }
    }
}