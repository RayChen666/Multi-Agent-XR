const dgram = require('dgram');
const wait = require('waait');

// Drone connection
const drone = {
  connection: dgram.createSocket('udp4'),
  host: '192.168.10.1',
  port: 8889,
  state: {
    connection: dgram.createSocket('udp4'),
    port: 8890,
    data: {} // Store latest state data without logging
  }
};

// Command delays (in milliseconds) - for timeout fallback only
const commandDelays = {
  command: 3000,      // Increased timeout for initialization
  takeoff: 7000,      // Increased for more reliable takeoff
  land: 7000,         // Increased for more reliable landing
  up: 7000,
  down: 7000,
  left: 5000,
  right: 5000,
  forward: 5000,
  back: 5000,
  ccw: 5000,          // Counterclockwise rotation
  cw: 5000,           // Clockwise rotation
  'rc': 2000,         // Remote control
  'battery?': 3000,   // Battery query
};

// Command queue to prevent overlapping commands
let commandQueue = [];
let isProcessingCommand = false;

// Parse state data without logging
function parseStateData(stateString) {
  const pairs = stateString.trim().split(';');
  const stateData = {};
  
  pairs.forEach(pair => {
    if (!pair) return;
    
    const [key, value] = pair.split(':');
    if (key && value) {
      stateData[key.trim()] = value.trim();
    }
  });
  
  return stateData;
}

// Error handler
function handleError(err) {
  if (err) {
    console.log('Drone command error:', err);
    return err;
  }
  return null;
}

// Initialize connections
function initConnections() {
  // Clear any previous listeners to avoid duplicates
  drone.connection.removeAllListeners('message');
  
  // Listen for drone responses (commands)
  drone.connection.on('message', message => {
    const response = message.toString();
    console.log(`Drone response: ${response}`);
    
    // Signal that the most recent command has received a response
    if (commandQueue.length > 0) {
      const currentCommand = commandQueue[0];
      
      // Check if the response is successful or an error
      if (response.startsWith('error')) {
        currentCommand.reject(new Error(response));
      } else {
        // For battery command, store the result
        if (currentCommand.command === 'battery?') {
          currentCommand.result = response.trim();
        }
        currentCommand.resolve({ 
          success: true, 
          response: response.trim(),
          result: currentCommand.result
        });
      }
    }
  });
  
  // Bind to receive responses
  drone.connection.bind(9000);
  
  // Listen for state updates (silently)
  drone.state.connection.on('message', message => {
    // Instead of logging, just update the internal state data
    const stateString = message.toString();
    drone.state.data = parseStateData(stateString);
    
    // Only log state updates if debug mode is enabled
    if (process.env.DRONE_DEBUG === 'true') {
      console.log(`Drone state: ${stateString}`);
    }
  });
  
  // Bind the state connection to receive state updates
  drone.state.connection.bind(8890);
  
  console.log('Drone socket connections initialized');
}

// Process the command queue
async function processCommandQueue() {
  if (isProcessingCommand || commandQueue.length === 0) return;
  
  isProcessingCommand = true;
  const commandObj = commandQueue[0];
  const { command, resolve, reject } = commandObj;
  
  // Get the base command (first word) for timeout calculation
  const baseCommand = command.split(' ')[0];
  const timeout = commandDelays[baseCommand] || 5000;
  
  console.log(`Sending command: ${command}, delay: ${timeout}ms`);
  
  // Set up timeout for command (fallback)
  const timeoutId = setTimeout(() => {
    console.log(`Command timeout: ${command}`);
    commandQueue.shift(); // Remove the first command from queue
    isProcessingCommand = false;
    reject(new Error(`Command ${command} timed out`));
    
    // Process next command in queue
    processCommandQueue();
  }, timeout);
  
  try {
    // Send the command to the drone
    drone.connection.send(
      command, 
      0, 
      command.length, 
      drone.port, 
      drone.host, 
      err => {
        if (err) {
          clearTimeout(timeoutId);
          console.error(`Command error: ${err}`);
          commandQueue.shift();
          isProcessingCommand = false;
          reject(err);
          processCommandQueue();
        }
        // Wait for response in the message event handler
      }
    );
  } catch (err) {
    clearTimeout(timeoutId);
    console.error(`Command exception: ${err}`);
    commandQueue.shift();
    isProcessingCommand = false;
    reject(err);
    processCommandQueue();
  }

  // Set up a listener to handle command completion
  const responseHandler = (success, result) => {
    clearTimeout(timeoutId);
    console.log(`Command completed: ${command}`);
    commandQueue.shift(); // Remove the command from queue
    isProcessingCommand = false;
    
    // Process the next command in queue
    processCommandQueue();
  };
  
  // Attach the response handler to the resolve/reject functions
  const originalResolve = commandObj.resolve;
  commandObj.resolve = (result) => {
    responseHandler(true, result);
    originalResolve(result);
  };
  
  const originalReject = commandObj.reject;
  commandObj.reject = (error) => {
    responseHandler(false, error);
    originalReject(error);
  };
}

// Send a command with appropriate delay
function sendCommand(command) {
  return new Promise((resolve, reject) => {
    // Add command to queue
    commandQueue.push({
      command,
      resolve,
      reject,
      result: null,
      timestamp: Date.now()
    });
    
    // Process queue (will do nothing if already processing)
    processCommandQueue();
  });
}

// Run multiple commands in sequence
async function runCommands(commands) {
  for (const command of commands) {
    try {
      await sendCommand(command);
    } catch (err) {
      console.error(`Error executing command ${command}:`, err);
    }
  }
}

// Start keep-alive
let keepAliveInterval = null;

function startKeepAlive() {
  if (keepAliveInterval) {
    clearInterval(keepAliveInterval);
  }
  
  console.log("Starting drone keep-alive signal");
  keepAliveInterval = setInterval(() => {
    sendCommand('rc 0 0 0 0').catch(err => {
      console.error("Keep-alive error:", err);
    });
  }, 8000);
}

function stopKeepAlive() {
  if (keepAliveInterval) {
    console.log("Stopping drone keep-alive signal");
    clearInterval(keepAliveInterval);
    keepAliveInterval = null;
  }
}

// Get the latest drone state data
function getStateData() {
  return { ...drone.state.data };
}

// Enable/disable debug mode (state logging)
function setDebugMode(enabled) {
  process.env.DRONE_DEBUG = enabled ? 'true' : 'false';
  console.log(`Drone debug mode ${enabled ? 'enabled' : 'disabled'}`);
}

// Reset the command queue (for emergency situations)
function resetCommandQueue() {
  const queueLength = commandQueue.length;
  commandQueue = [];
  isProcessingCommand = false;
  console.log(`Command queue reset. ${queueLength} commands removed.`);
  return queueLength;
}

module.exports = {
  initConnections,
  sendCommand,
  runCommands,
  startKeepAlive,
  stopKeepAlive,
  getStateData,
  setDebugMode,
  resetCommandQueue
};