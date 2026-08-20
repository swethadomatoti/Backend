from datetime import timedelta
import re

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model
from django.conf import settings
import random
import string
from .permissions import IsAdminOnly
from accounts.permissions import IsStudent
from .tasks import (
    send_admin_enrollment_email_sync,
    send_student_approval_email_sync,
    send_student_rejection_email_sync,
)
import hmac
import hashlib
from django.utils import timezone
from .models import Enrollment, PaymentDetail, PaymentTransaction
from .serializers import EnrollmentSerializer, PaymentDetailSerializer
from accounts.models import StudentProfile

import razorpay

from django.http import HttpResponse
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle





User = get_user_model()


# ----------------ENROLL View---------------- #

class EnrollView(APIView):

    def post(self, request):
        courses = request.data.get('courses')
        course = request.data.get('course')

        if courses and isinstance(courses, list):
            course_list = courses
        elif course:
            course_list = [course]
        else:
            return Response({"error": "No course provided."}, status=status.HTTP_400_BAD_REQUEST)

        created_enrollments = []
        errors = []
        email_warnings = []

        for c in course_list:
            data = request.data.copy()
            data['course'] = c
            if 'courses' in data:
                del data['courses']

            serializer = EnrollmentSerializer(data=data)

            if serializer.is_valid():
                try: 
                    enrollment = serializer.save()

                    try:
                        send_admin_enrollment_email_sync(
                            enrollment.name,
                            enrollment.email,
                            enrollment.course.title,
                            enrollment.phone
                        )
                    except Exception as email_error:
                        email_warnings.append(f"Notification error for {c}: {str(email_error)}")

                    created_enrollments.append(enrollment)
                except Exception as e:
                    errors.append({"course": c, "error": "Failed to create enrollment", "details": str(e)})
            else:
                errors.append({"course": c, "errors": serializer.errors})

        if errors and not created_enrollments:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        # Build response payload
        payload = {
            "message": "Enrollment successful",
            "enrollments": [e.id for e in created_enrollments],
            "redirect": "payment"
        }
        
        # Maintain backward compatibility for single course response
        if len(created_enrollments) == 1:
            payload["enrollment_id"] = created_enrollments[0].id
            
        if email_warnings:
            payload["warnings"] = email_warnings
        if errors:
            payload["errors"] = errors
            return Response(payload, status=status.HTTP_207_MULTI_STATUS)

        return Response(payload, status=status.HTTP_201_CREATED)

   

    
# ---------------- Enrollment LIST ---------------- #

class EnrollmentListView(APIView):
    def get(self, request):
        enrollments = Enrollment.objects.all().order_by("-created_at")
        serializer = EnrollmentSerializer(enrollments, many=True)
        return Response(serializer.data)


# ---------------- APPROVE ---------------- #

class ApproveEnrollmentView(APIView):
    permission_classes = [IsAdminOnly]
    def post(self, request, id):
        try:
            enrollment = Enrollment.objects.get(id=id)
        except Enrollment.DoesNotExist:
            return Response({"error": "Enrollment not found"}, status=status.HTTP_404_NOT_FOUND)

        # ✅ Already approved check
        if enrollment.status == "approved":
            return Response({"message": "Already approved"}, status=status.HTTP_200_OK)

        try:
            enrollment.status = "approved"
            enrollment.is_active = True
            enrollment.save()

            return Response({
                "message": "Enrollment approved successfully. Confirmation email sent to student."
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": "Failed to approve enrollment", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ---------------- REJECT ---------------- #

class RejectEnrollmentView(APIView):
    permission_classes = [IsAdminOnly]

    def post(self, request, id):
        try:
            enrollment = Enrollment.objects.get(id=id)
        except Enrollment.DoesNotExist:
            return Response({"error": "Enrollment not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            # ✅ Check if already rejected
            if enrollment.status == "rejected":
                return Response({"message": "Already rejected"}, status=status.HTTP_200_OK)

            enrollment.status = "rejected"
            enrollment.save()

            email_warning = None
            try:
                send_student_rejection_email_sync(
                    enrollment.name,
                    enrollment.course.title,
                    enrollment.email
                )
            except Exception as email_error:
                email_warning = f"Enrollment rejected, but student email failed: {str(email_error)}"

            payload = {"message": "Enrollment rejected successfully."}
            if email_warning:
                payload["warning"] = email_warning
            else:
                payload["message"] = "Enrollment rejected successfully. Student has been notified."

            return Response(payload, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {"error": "Failed to reject enrollment", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )



#_________________________________________________
#           Razorpay
#_________________________________________________


class CreateOrderView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        amount = request.data.get("amount")  # in rupees
        enrollment_id = request.data.get("enrollment_id")

        if enrollment_id:
            try:
                enrollment = Enrollment.objects.get(id=enrollment_id)
                if enrollment.status != 'approved':
                    return Response(
                        {"error": "Payment can only be initiated for approved enrollments."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                # If no amount is provided, default to the course price or remaining balance
                if not amount:
                    payment_detail = enrollment.payment_details.first()
                    if payment_detail and payment_detail.remaining_balance > 0:
                        amount = payment_detail.remaining_balance
                    elif enrollment.course and enrollment.course.price:
                        amount = enrollment.course.price
            except Enrollment.DoesNotExist:
                return Response({"error": "Enrollment not found."}, status=status.HTTP_404_NOT_FOUND)

        if not amount:
            return Response({"error": "Amount is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            client = razorpay.Client(auth=(
                settings.RAZORPAY_KEY_ID,
                settings.RAZORPAY_KEY_SECRET
            ))

            order = client.order.create({
                "amount": int(float(amount) * 100),  # convert to paisa securely
                "currency": "INR",
                "payment_capture": 1
            })

            return Response({
                "order_id": order["id"],
                "amount": order["amount"],
                "key": settings.RAZORPAY_KEY_ID
            })
        except razorpay.errors.BadRequestError as e:
            return Response(
                {"error": f"Razorpay Bad Request: {str(e)}", "details": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": "Failed to create order", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )





class VerifyPaymentView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        data = request.data

        razorpay_order_id = data.get("razorpay_order_id")
        razorpay_payment_id = data.get("razorpay_payment_id")
        razorpay_signature = data.get("razorpay_signature")
        enrollment_id = data.get("enrollment_id")
        enrollment_ids = data.get("enrollment_ids")

        # 🔒 Generate signature
        generated_signature = hmac.new(
            bytes(settings.RAZORPAY_KEY_SECRET, 'utf-8'),
            bytes(f"{razorpay_order_id}|{razorpay_payment_id}", 'utf-8'),
            hashlib.sha256
        ).hexdigest()

        # ✅ Verify payment
        if generated_signature == razorpay_signature:
            # Handle multiple or single enrollments
            ids_to_update = enrollment_ids if enrollment_ids else ([enrollment_id] if enrollment_id else [])
            
            if not ids_to_update:
                return Response({"error": "No enrollment ID provided"}, status=400)

            # 🔥 Fetch secure order details directly from Razorpay
            try:
                client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
                order_info = client.order.fetch(razorpay_order_id)
                amount_paid = order_info.get("amount", 0) / 100.0  # convert paisa to rupees
            except Exception:
                amount_paid = data.get("amount") # fallback to frontend payload if fetch fails

            updated = 0
            for e_id in ids_to_update:
                try:
                    enrollment = Enrollment.objects.get(id=e_id)
                    # 🔥 Update enrollment
                    enrollment.payment_status = "paid"
                    enrollment.razorpay_order_id = razorpay_order_id
                    enrollment.razorpay_payment_id = razorpay_payment_id
                    enrollment.save()
                    
                    # 🔥 Update PaymentDetail
                    payment_detail, _ = PaymentDetail.objects.get_or_create(enrollment=enrollment)
                    if amount_paid:
                        increment = float(amount_paid)
                    else:
                        increment = float(payment_detail.fee_amount) if payment_detail.fee_amount else float(enrollment.course.price)
                    payment_detail.payment_paid = float(payment_detail.payment_paid) + increment
                    payment_detail.save()

                    PaymentTransaction.objects.create(payment_detail=payment_detail, amount=increment)

                    updated += 1
                except Enrollment.DoesNotExist:
                    continue

            return Response({
                "message": f"Payment verified & {updated} enrollment(s) updated"
            })

        else:
            return Response({
                "error": "Payment verification failed"
            }, status=400)

#**********Student CoursesView**********************
           
from rest_framework.permissions import IsAuthenticated
from .serializers import StudentEnrolledCourseSerializer
from learning.models import LessonProgress
from courses.models import Batch

class StudentCoursesView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        email = request.user.email
        enrollments = Enrollment.objects.filter(email=email, status='approved', is_active=True)
        serializer = StudentEnrolledCourseSerializer(enrollments, many=True, context={'request': request})
        return Response(serializer.data)
#----------------------------------------------
# StudentDashboardStatsView
#----------------------------------------------
class StudentDashboardStatsView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        email = request.user.email
        enrolled_count = Enrollment.objects.filter(email=email, status='approved', is_active=True).count()
        completed_lessons = LessonProgress.objects.filter(student=request.user, completed=True).count()
        
        active_batches = Batch.objects.filter(students=request.user, is_live_class_active=True)
        active_live_classes = []
        for b in active_batches:
            active_live_classes.append({
                'course_title': b.course.title,
                'batch_name': b.name,
                'live_link': b.live_link
            })
            
        return Response({
            'enrolled_courses_count': enrolled_count,
            'completed_lessons_count': completed_lessons,
            'next_live_session': 'Saturday, 10:00 AM',
            'active_live_classes': active_live_classes
        })

# ---------------- CHOICE VIEWS ---------------- #

class FeeStatusChoicesView(APIView):
    def get(self, request):
        return Response([{"key": c[0], "label": c[1]} for c in [
            ('Pending', 'Pending'),
            ('Paid', 'Paid'),
            ('Partially Paid', 'Partially Paid'),
        ]])

class StatusChoicesView(APIView):
    def get(self, request):
        return Response([{"key": c[0], "label": c[1]} for c in Enrollment.STATUS_CHOICES])

class CourseTypeChoicesView(APIView):
    def get(self, request):
        return Response([{"key": c[0], "label": c[1]} for c in Enrollment.COURSE_TYPE_CHOICES])

class ModeChoicesView(APIView):
    def get(self, request):
        return Response([{"key": c[0], "label": c[1]} for c in Enrollment.MODE_CHOICES])

class TimingChoicesView(APIView):
    def get(self, request):
        return Response([{"key": c[0], "label": c[1]} for c in Enrollment.TIMING_CHOICES])

class ExperienceChoicesView(APIView):
    def get(self, request):
        return Response([{"key": c[0], "label": c[1]} for c in Enrollment.EXPERIENCE_CHOICES])

class CurrentStatusChoicesView(APIView):
    def get(self, request):
        return Response([{"key": c[0], "label": c[1]} for c in Enrollment.CURRENT_STATUS_CHOICES])

# ---------------- DETAIL CRUD ---------------- #

class EnrollmentDetailCRUDView(APIView):
    def get_object(self, id):
        try:
            return Enrollment.objects.get(id=id)
        except Enrollment.DoesNotExist:
            return None

    def get(self, request, id):
        enrollment = self.get_object(id)
        if not enrollment:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = EnrollmentSerializer(enrollment)
        return Response(serializer.data)

    def put(self, request, id):
        enrollment = self.get_object(id)
        if not enrollment:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = EnrollmentSerializer(enrollment, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, id):
        enrollment = self.get_object(id)
        if not enrollment:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = EnrollmentSerializer(enrollment, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, id):
        enrollment = self.get_object(id)
        if not enrollment:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        enrollment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------- PAYMENT DETAILS CRUD ---------------- #

class EnrollmentPaymentDetailView(APIView):
    def get(self, request, enrollment_id):
        try:
            enrollment = Enrollment.objects.get(id=enrollment_id)
        except Enrollment.DoesNotExist:
            return Response({"error": "Enrollment not found"}, status=status.HTTP_404_NOT_FOUND)
            
        # Get existing payment details or create a default one (which triggers auto-calculation of fees)
        payment, created = PaymentDetail.objects.get_or_create(enrollment=enrollment)
        
        serializer = PaymentDetailSerializer(payment)
        return Response(serializer.data)

    def post(self, request, enrollment_id):
        try:
            enrollment = Enrollment.objects.get(id=enrollment_id)
        except Enrollment.DoesNotExist:
            return Response({"error": "Enrollment not found"}, status=status.HTTP_404_NOT_FOUND)

        # Every call here records a NEW payment on top of whatever has already
        # been paid, rather than overwriting the total - so the admin only
        # ever enters the amount being paid right now.
        try:
            amount = float(request.data.get('payment_paid'))
        except (TypeError, ValueError):
            return Response({"error": "A valid payment amount is required."}, status=status.HTTP_400_BAD_REQUEST)

        if amount <= 0:
            return Response({"error": "Payment amount must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

        payment, _ = PaymentDetail.objects.get_or_create(enrollment=enrollment)
        payment.payment_paid = float(payment.payment_paid) + amount
        payment.save()  # recalculates remaining_balance and enrollment.fee_status

        PaymentTransaction.objects.create(payment_detail=payment, amount=amount)

        serializer = PaymentDetailSerializer(payment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def put(self, request, enrollment_id):
        payment = PaymentDetail.objects.filter(enrollment_id=enrollment_id).first()
        if not payment:
            return Response({"error": "Payment details not found"}, status=status.HTTP_404_NOT_FOUND)
            
        serializer = PaymentDetailSerializer(payment, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, enrollment_id):
        payment = PaymentDetail.objects.filter(enrollment_id=enrollment_id).first()
        if not payment:
            return Response({"error": "Payment details not found"}, status=status.HTTP_404_NOT_FOUND)
            
        serializer = PaymentDetailSerializer(payment, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, enrollment_id):
        payment = PaymentDetail.objects.filter(enrollment_id=enrollment_id).first()
        if not payment:
            return Response({"error": "Payment details not found"}, status=status.HTTP_404_NOT_FOUND)
            
        payment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------- INVOICE ---------------- #

class GenerateInvoiceView(APIView):
    def get(self, request, enrollment_id):
        try:
            enrollment = Enrollment.objects.get(id=enrollment_id)
        except Enrollment.DoesNotExist:
            return Response({"error": "Enrollment not found"}, status=status.HTTP_404_NOT_FOUND)
            
        payment = PaymentDetail.objects.filter(enrollment=enrollment).first()
        
        # Determine format (json or pdf) - use 'export' as parameter to avoid DRF format conflicts
        output_format = request.query_params.get('export', 'json')
        is_preview = request.query_params.get('preview', 'false').lower() == 'true'
        
        # Use enrollment creation date to ensure the invoice number and date stay the same on every download
        base_date = enrollment.created_at if enrollment.created_at else timezone.now()
        
        invoice_number = f"INV-{enrollment.id}-{base_date.strftime('%Y%m%d%H%M%S')}"
        invoice_date = base_date.strftime("%Y-%m-%d %H:%M:%S")
        
        total_fee = payment.fee_amount if payment else (enrollment.course.price if enrollment.course else 0)
        amount_paid = payment.payment_paid if payment else 0
        balance_remaining = payment.remaining_balance if payment else (enrollment.course.price if enrollment.course else 0)
        
        if output_format.lower() == 'pdf' or is_preview:
            # Generate PDF
            response = HttpResponse(content_type='application/pdf')
            disposition = "inline" if is_preview else "attachment"
            response['Content-Disposition'] = f'{disposition}; filename="Invoice_{invoice_number}.pdf"'
            
            p = canvas.Canvas(response, pagesize=A4)
            width, height = A4
            
            # Colors
            primary_color = colors.HexColor("#062854")  # Dark blue for NxGen
            bg_color = colors.HexColor("#EAF1F8")       # Light blueish for total background
            border_color = colors.HexColor("#E0E0E0")
            
            # --- Header (Blue Box) ---
            p.setFillColor(primary_color)
            p.roundRect(20, height - 140, width - 40, 120, radius=8, fill=1, stroke=0)
            
            # White Logo Box
            p.setFillColor(colors.white)
            p.roundRect(40, height - 110, 140, 60, radius=6, fill=1, stroke=0)
            
            # NxGen Logo Text inside white box
            p.setFillColor(primary_color)
            p.setFont("Helvetica-Bold", 26)
            p.drawString(50, height - 75, "NxGen")
            p.setFont("Helvetica-Bold", 10)
            p.drawString(50, height - 90, "TECH ACADEMY")
            
            # Address Text in Blue Box
            p.setFillColor(colors.white)
            p.setFont("Helvetica", 9)
            p.drawString(200, height - 70, "First Floor, 1-121/63 Survey No. 63")
            p.drawString(200, height - 85, "Part Hotel Sitara Grand Backside,")
            p.drawString(200, height - 100, "Miyapur, Telangana 500049")
            
            # Right side Invoice Info
            p.setFont("Helvetica-Bold", 32)
            p.drawRightString(width - 40, height - 65, "INVOICE")
            
            p.setFont("Helvetica", 10)
            p.drawRightString(width - 40, height - 90, f"No:   {invoice_number}")
            p.drawRightString(width - 40, height - 105, f"Date:   {getattr(base_date, 'strftime', lambda x: invoice_date)('%Y-%m-%d %H:%M:%S')}")
            
            # --- Bill To & Details ---
            # Left side: BILL TO
            y_start = height - 160
            p.setFont("Helvetica-Bold", 10)
            p.setFillColor(primary_color)
            p.drawString(40, y_start, "BILL TO")
            
            p.setFillColor(colors.black)
            
            # Details function
            def draw_kv(x, y, k, v):
                p.setFont("Helvetica-Bold", 9)
                p.drawString(x, y, k)
                p.setFont("Helvetica", 9)
                p.drawString(x + 80, y, ":  " + str(v))
                
            draw_kv(40, y_start - 20, "Student Name", enrollment.name)
            draw_kv(40, y_start - 35, "Phone", enrollment.phone)
            draw_kv(40, y_start - 50, "Email", enrollment.email)
            draw_kv(40, y_start - 65, "Enroll Date", getattr(enrollment.created_at, 'strftime', lambda x: "")("%d %b %Y") if enrollment.created_at else "N/A")
            
            # Right side: Student/Course Data (inside a rounded rect)
            rx = width / 2 + 20
            ry = y_start - 75
            rw = width / 2 - 60
            rh = 85
            
            p.setStrokeColor(border_color)
            p.setFillColor(colors.white)
            p.roundRect(rx, ry, rw, rh, 5, fill=1)
            
            p.setFillColor(colors.black)
            draw_kv(rx + 10, ry + 65, "Course", enrollment.course.title if enrollment.course else 'N/A')
            draw_kv(rx + 10, ry + 50, "Type", getattr(enrollment, 'course_type', 'N/A'))
            draw_kv(rx + 10, ry + 35, "Mode", getattr(enrollment, 'preferred_mode', 'N/A'))
            
            if enrollment.razorpay_payment_id:
                draw_kv(rx + 10, ry + 20, "Payment ID", enrollment.razorpay_payment_id)
            elif enrollment.razorpay_order_id:
                draw_kv(rx + 10, ry + 20, "Order ID", enrollment.razorpay_order_id[:15] + "..")
            else:
                 draw_kv(rx + 10, ry + 20, "Payment Mode", "Online")
            
            # --- Items Table ---
            y_curr = ry - 30
            
            table_data = [
                ["#", "DESCRIPTION", "", "AMOUNT (INR)"]
            ]
            
            # Main item
            course_title = enrollment.course.title if enrollment.course else 'Course Fee'
            table_data.append(["1", course_title, "", f"Rs. {float(total_fee):,.2f}"])
            
            # Blank rows to fill space
            table_data.append(["", "", "", ""])
            
            # Totals
            table_data.append(["", "", "Subtotal", f"Rs. {float(total_fee):,.2f}"])
            table_data.append(["", "", "Amount Paid", f"Rs. {float(amount_paid):,.2f}"])
            
            table = Table(table_data, colWidths=[30, 250, 100, 135])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
                ('ALIGN', (-2, 0), (-2, -1), 'RIGHT'),
                
                # Rows style
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                
                # Border
                ('GRID', (0, 0), (-1, -1), 0.5, border_color),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                
                # Bottom Row (Totals padding)
                ('SPAN', (0, -1), (1, -1)),
                ('SPAN', (0, -2), (1, -2)),
                ('ALIGN', (-2, -1), (-2, -1), 'RIGHT'),
                ('ALIGN', (-2, -2), (-2, -2), 'RIGHT'),
            ]))
            
            tw, th = table.wrap(width - 80, height)
            table.drawOn(p, 40, y_curr - th)
            
            # Balance row below table
            y_curr = y_curr - th
            
            table_bal = Table([
                ["", "", "Balance Remaining", f"Rs. {float(balance_remaining):,.2f}"]
            ], colWidths=[30, 250, 100, 135])
            
            table_bal.setStyle(TableStyle([
                ('BACKGROUND', (-2, 0), (-1, 0), bg_color),
                ('TEXTCOLOR', (-2, 0), (-1, 0), colors.black),
                ('FONTNAME', (-2, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('ALIGN', (-1, 0), (-1, 0), 'RIGHT'),
                ('ALIGN', (-2, 0), (-2, 0), 'RIGHT'),
                ('GRID', (-2, 0), (-1, 0), 0.5, border_color),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
            ]))
            
            bw, bh = table_bal.wrap(width - 80, height)
            table_bal.drawOn(p, 40, y_curr - bh)
            y_curr -= bh
            
            # --- Payment Info & Signatory ---
            y_curr -= 50
            p.setFont("Helvetica-Bold", 10)
            p.setFillColor(primary_color)
            p.drawString(40, y_curr, "PAYMENT INFORMATION")
            
            p.setFont("Helvetica", 10)
            p.setFillColor(colors.black)
            p.drawString(40, y_curr - 17, "Thank you for your payment. Your fee has been recorded.")
            draw_kv(40, y_curr - 37, "Payment Status", enrollment.fee_status)
            draw_kv(40, y_curr - 52, "Paid Amount", f"Rs. {float(amount_paid):,.2f}")
            
            # Terms
            y_curr -= 85
            p.setFont("Helvetica-Bold", 11)
            p.setFillColor(primary_color)
            p.drawString(40, y_curr, "TERMS & CONDITIONS")
            p.setFont("Helvetica", 10)
            p.setFillColor(colors.black)
            p.drawString(40, y_curr - 18, "• This invoice is computer generated and does not require a signature.")
            p.drawString(40, y_curr - 32, "• Fees once paid are non-refundable & non-transferable.")
            p.drawString(40, y_curr - 46, f"• Please quote your Invoice No. ({invoice_number}) for any future correspondence.")
            
            # --- Footer (Blue bar at bottom) ---
            p.setFillColor(primary_color)
            p.roundRect(20, 20, width - 40, 50, radius=8, fill=1, stroke=0)
            
            p.setFillColor(colors.white)
            
            # Phone Section
            px = width / 2 - 170
            py = 45
            p.setStrokeColor(colors.white)
            p.setLineWidth(1.5)
            # Draw a tiny phone outline
            p.roundRect(px-7, py-8, 12, 18, radius=2, stroke=1, fill=0)
            p.circle(px-1, py-5, 1, stroke=1, fill=1)
            
            p.setFont("Helvetica-Bold", 11)
            p.drawString(px + 15, py - 3, "9701314138")
            
            # Web Section
            wx = width / 2 + 10
            # Draw a tiny globe outline
            p.circle(wx, py+1, 9, stroke=1, fill=0)
            p.line(wx-9, py+1, wx+9, py+1)
            p.line(wx, py-8, wx, py+10)
            p.ellipse(wx-4, py-8, wx+4, py+10, stroke=1, fill=0)
            
            p.drawString(wx + 15, py - 3, "https://nxgentechacademy.com/")
            
            p.showPage()
            p.save()
            return response
            
        else:
            # Generate JSON Output
            invoice_data = {
                "invoice_number": invoice_number,
                "date": invoice_date,
                "student_details": {
                    "name": enrollment.name,
                    "email": enrollment.email,
                    "phone": enrollment.phone
                },
                "course_details": {
                    "title": enrollment.course.title if enrollment.course else None,
                    "type": getattr(enrollment, 'course_type', None),
                    "mode": getattr(enrollment, 'preferred_mode', None)
                },
                "payment_details": {
                    "total_fee": total_fee,
                    "amount_paid": amount_paid,
                    "balance_remaining": balance_remaining,
                    "payment_status": enrollment.payment_status,
                    "fee_status": enrollment.fee_status,
                },
                "transaction_details": {
                    "razorpay_order_id": enrollment.razorpay_order_id,
                    "razorpay_payment_id": enrollment.razorpay_payment_id,
                }
            }
            
            return Response(invoice_data, status=status.HTTP_200_OK)


from django.db.models import Q

class PaidInvoicesListView(APIView):
    def get(self, request):
        # Fetch enrollments where a payment has been made
        enrollments = Enrollment.objects.filter(
            Q(fee_status__in=['Paid', 'Partially Paid']) | 
            Q(payment_status__iexact='paid') | 
            Q(payment_details__payment_paid__gt=0)
        ).distinct().order_by('-created_at')

        data = []
        for enrollment in enrollments:
            payment = enrollment.payment_details.first()
            base_date = enrollment.created_at if enrollment.created_at else timezone.now()
            
            data.append({
                "enrollment_id": enrollment.id,
                "invoice_number": f"INV-{enrollment.id}-{base_date.strftime('%Y%m%d%H%M%S')}",
                "student_name": enrollment.name,
                "student_email": enrollment.email,
                "course_title": enrollment.course.title if enrollment.course else "N/A",
                "amount_paid": payment.payment_paid if payment else 0,
                "balance_remaining": payment.remaining_balance if payment else (enrollment.course.price if enrollment.course else 0),
                "fee_status": enrollment.fee_status,
                "download_pdf_url": f"/api/enrollments/{enrollment.id}/invoice/?export=pdf",
                "preview_pdf_url": f"/api/enrollments/{enrollment.id}/invoice/?preview=true",
                "json_details_url": f"/api/enrollments/{enrollment.id}/invoice/"
            })
        
        return Response(data, status=status.HTTP_200_OK)

