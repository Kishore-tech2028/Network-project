import socket
import ssl
import json
from django.http import JsonResponse # Added this import
from django.shortcuts import render

def home(request):
    return render(request, 'index.html')

# CN/Quiz_System/views.py
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
import socket
import ssl

@csrf_exempt # For testing; in production use proper CSRF handling
def get_live_score(request):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection(('127.0.0.1', 12345)) as sock:
            with context.wrap_socket(sock, server_hostname='127.0.0.1') as ssock:
                socket_file = ssock.makefile('rw')
                
                # 1. Identity
                socket_file.write("WebPlayer\n")
                socket_file.flush()
                
                if request.method == 'POST':
                    # Logic to handle answer submission
                    user_data = json.loads(request.body)
                    user_answer = user_data.get('answer')
                    
                    # We need to skip to the right question state 
                    # Note: In a real app, you'd use sessions to track which question the user is on
                    socket_file.readline() # Read first question
                    socket_file.write(f"{user_answer}\n")
                    socket_file.flush()
                    
                    result_line = socket_file.readline()
                    return JsonResponse(json.loads(result_line))

                # GET Request: Just fetch the current question
                line = socket_file.readline()
                return JsonResponse(json.loads(line))
                
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)