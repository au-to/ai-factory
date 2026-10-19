const { ChatGPTAPI } = require('chatgpt');

const api = new ChatGPTAPI({
  apiKey: process.env.OPENAI_API_KEY,
});

const response = await api.sendMessage('Hello, how are you?');
console.log(response);